//! Length-delimited control-center handshake, framed requests, and a raw MCP answer stream.

use crate::server::ServerOptions;
use crate::session::SessionEnvironment;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

const PROTOCOL_VERSION: u32 = 1;
const MAX_HANDSHAKE_BYTES: usize = 16 * 1024 * 1024;
/// Largest payload the proxy places in one request frame, and the buffer the control center reads
/// it back into.
pub(crate) const MAX_REQUEST_FRAME_BYTES: usize = 64 * 1024;

#[derive(Debug, Deserialize, Serialize)]
pub(crate) struct Handshake {
    protocol_version: u32,
    pub(crate) options: ServerOptions,
    pub(crate) environment: SessionEnvironment,
}

impl Handshake {
    pub(crate) fn new(options: ServerOptions, environment: SessionEnvironment) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            options,
            environment,
        }
    }

    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(format!(
                "Unsupported control-center protocol version {}; expected {}.",
                self.protocol_version, PROTOCOL_VERSION
            ));
        }
        if !self.environment.cwd().is_absolute() {
            return Err(
                "The control-center handshake contained a non-absolute working directory."
                    .to_string(),
            );
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize, Serialize)]
struct HandshakeResponse {
    error: Option<String>,
}

pub(crate) async fn write_handshake(
    stream: &mut (impl AsyncWrite + Unpin),
    handshake: &Handshake,
) -> Result<(), String> {
    write_frame(stream, handshake, "control-center handshake").await
}

pub(crate) async fn read_handshake(
    stream: &mut (impl AsyncRead + Unpin),
) -> Result<Handshake, String> {
    let handshake: Handshake = read_frame(stream, "control-center handshake").await?;
    handshake.validate()?;
    Ok(handshake)
}

pub(crate) async fn write_handshake_success(
    stream: &mut (impl AsyncWrite + Unpin),
) -> Result<(), String> {
    write_frame(
        stream,
        &HandshakeResponse { error: None },
        "control-center handshake response",
    )
    .await
}

pub(crate) async fn write_handshake_error(
    stream: &mut (impl AsyncWrite + Unpin),
    error: String,
) -> Result<(), String> {
    write_frame(
        stream,
        &HandshakeResponse { error: Some(error) },
        "control-center handshake response",
    )
    .await
}

pub(crate) async fn read_handshake_response(
    stream: &mut (impl AsyncRead + Unpin),
) -> Result<(), String> {
    let response: HandshakeResponse =
        read_frame(stream, "control-center handshake response").await?;
    match response.error {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

/// Streams MCP stdin to the control center as length-prefixed frames, ending with a zero-length
/// frame once stdin closes.
///
/// A raw byte stream cannot say "no more requests" on every platform: Unix half-closes a socket,
/// Windows named pipes have no equivalent. Without that mark the control center holds a finished
/// session open, and a proxy whose stdin closed would have to guess how long to wait for the
/// answers it is still owed — a guess that is either too short to deliver them or too long to
/// shut down promptly. The explicit end-of-input frame removes the guess. Both ends always come
/// from the same build, because the endpoint name carries the build id, so the framing needs no
/// negotiation.
/// `forwarded` records that at least one request byte reached the control center, which is how the
/// caller tells "answers are owed" from "the client never asked anything".
pub(crate) async fn forward_requests(
    input: &mut (impl AsyncRead + Unpin),
    output: &mut (impl AsyncWrite + Unpin),
    forwarded: &AtomicBool,
) -> std::io::Result<()> {
    let mut buffer = vec![0_u8; MAX_REQUEST_FRAME_BYTES];
    loop {
        let read = input.read(&mut buffer).await?;
        let length =
            u32::try_from(read).expect("a single read never exceeds the request frame buffer");
        output.write_all(&length.to_be_bytes()).await?;
        if read == 0 {
            return output.flush().await;
        }
        output.write_all(&buffer[..read]).await?;
        forwarded.store(true, Ordering::Release);
        output.flush().await?;
    }
}

/// Reassembles framed requests into the plain byte stream the MCP server reads.
///
/// Returns once the proxy marks the end of input, which is what makes the server observe EOF and
/// end the session.
pub(crate) async fn receive_requests(
    input: &mut (impl AsyncRead + Unpin),
    output: &mut (impl AsyncWrite + Unpin),
) -> Result<(), String> {
    let mut header = [0_u8; 4];
    let mut payload = vec![0_u8; MAX_REQUEST_FRAME_BYTES];
    loop {
        match input.read_exact(&mut header).await {
            Ok(_) => {}
            // A proxy that died without marking the end of input tells the server the same thing:
            // nothing more is coming.
            Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(()),
            Err(error) => return Err(format!("Cannot read the request frame length: {error}")),
        }
        let length = u32::from_be_bytes(header) as usize;
        if length == 0 {
            return Ok(());
        }
        if length > MAX_REQUEST_FRAME_BYTES {
            return Err(format!(
                "A {length}-byte request frame exceeds the {MAX_REQUEST_FRAME_BYTES}-byte limit."
            ));
        }
        input
            .read_exact(&mut payload[..length])
            .await
            .map_err(|error| format!("Cannot read a {length}-byte request frame: {error}"))?;
        output
            .write_all(&payload[..length])
            .await
            .map_err(|error| format!("Cannot hand a request frame to the MCP server: {error}"))?;
        output
            .flush()
            .await
            .map_err(|error| format!("Cannot flush a request frame to the MCP server: {error}"))?;
    }
}

async fn write_frame<T: Serialize>(
    stream: &mut (impl AsyncWrite + Unpin),
    value: &T,
    label: &str,
) -> Result<(), String> {
    let bytes =
        serde_json::to_vec(value).map_err(|error| format!("Cannot encode the {label}: {error}"))?;
    if bytes.len() > MAX_HANDSHAKE_BYTES {
        return Err(format!(
            "Cannot send the {label}: {} bytes exceeds the {}-byte safety limit.",
            bytes.len(),
            MAX_HANDSHAKE_BYTES
        ));
    }
    let length = u32::try_from(bytes.len())
        .map_err(|_| format!("Cannot send the {label}: its length cannot be represented."))?;
    stream
        .write_all(&length.to_be_bytes())
        .await
        .map_err(|error| format!("Cannot write the {label} length: {error}"))?;
    stream
        .write_all(&bytes)
        .await
        .map_err(|error| format!("Cannot write the {label} body: {error}"))?;
    stream
        .flush()
        .await
        .map_err(|error| format!("Cannot flush the {label}: {error}"))
}

async fn read_frame<T: for<'de> Deserialize<'de>>(
    stream: &mut (impl AsyncRead + Unpin),
    label: &str,
) -> Result<T, String> {
    let mut length = [0_u8; 4];
    stream
        .read_exact(&mut length)
        .await
        .map_err(|error| format!("Cannot read the {label} length: {error}"))?;
    let length = u32::from_be_bytes(length) as usize;
    if length > MAX_HANDSHAKE_BYTES {
        return Err(format!(
            "Cannot read the {label}: {length} bytes exceeds the {MAX_HANDSHAKE_BYTES}-byte safety limit."
        ));
    }
    let mut bytes = vec![0_u8; length];
    stream
        .read_exact(&mut bytes)
        .await
        .map_err(|error| format!("Cannot read the {label} body: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Cannot parse the {label}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        Handshake, read_handshake, read_handshake_response, write_handshake,
        write_handshake_success,
    };
    use crate::server::ServerOptions;
    use crate::session::SessionEnvironment;
    use std::ffi::OsString;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn handshake_is_framed_without_consuming_following_mcp_bytes() {
        let (mut client, mut server) = tokio::io::duplex(64 * 1024);
        let handshake = Handshake::new(
            ServerOptions { enable_shell: true },
            SessionEnvironment::new(
                std::env::current_dir().unwrap(),
                vec![(OsString::from("PATH"), OsString::from("sentinel"))],
            ),
        );
        let client_task = tokio::spawn(async move {
            write_handshake(&mut client, &handshake).await.unwrap();
            client.write_all(b"mcp\n").await.unwrap();
            read_handshake_response(&mut client).await.unwrap();
        });

        let decoded = read_handshake(&mut server).await.unwrap();
        assert!(decoded.options.enable_shell);
        let mut tail = [0_u8; 4];
        server.read_exact(&mut tail).await.unwrap();
        assert_eq!(&tail, b"mcp\n");
        write_handshake_success(&mut server).await.unwrap();
        client_task.await.unwrap();
    }
}
