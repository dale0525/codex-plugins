use std::io::{self, Write};
use std::process::Command;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use keyring::Entry;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};

const KEYRING_SERVICE: &str = "codex-sync";
const KEYRING_ACCOUNT: &str = "github.com";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Credential {
    pub access_token: String,
    #[serde(default)]
    pub expires_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub refresh_token: Option<String>,
    #[serde(default)]
    pub refresh_expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Deserialize)]
struct DeviceCode {
    device_code: String,
    user_code: String,
    verification_uri: String,
    expires_in: u64,
    interval: u64,
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    #[serde(default)]
    access_token: Option<String>,
    #[serde(default)]
    expires_in: Option<i64>,
    #[serde(default)]
    refresh_token: Option<String>,
    #[serde(default)]
    refresh_token_expires_in: Option<i64>,
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    error_description: Option<String>,
}

pub fn resolve_token(client: &Client, client_id: Option<&str>) -> Result<String> {
    if let Ok(token) = std::env::var("CODEX_SYNC_GITHUB_TOKEN") {
        let token = token.trim();
        if !token.is_empty() {
            return Ok(token.to_owned());
        }
    }
    if let Some(mut credential) = load_credential()? {
        if credential
            .expires_at
            .is_none_or(|value| value > Utc::now() + ChronoDuration::minutes(5))
        {
            return Ok(credential.access_token);
        }
        if let (Some(client_id), Some(_)) = (client_id, credential.refresh_token.as_deref()) {
            if credential
                .refresh_expires_at
                .is_some_and(|expires| expires <= Utc::now() + ChronoDuration::minutes(5))
            {
                anyhow::bail!("GitHub refresh token expired; run `codex-sync login` again");
            }
            credential = refresh(client, client_id, &credential)?;
            store_credential(&credential)?;
            return Ok(credential.access_token);
        }
    }
    if let Ok(output) = Command::new("gh").args(["auth", "token"]).output() {
        if output.status.success() {
            let token = String::from_utf8_lossy(&output.stdout).trim().to_owned();
            if !token.is_empty() {
                return Ok(token);
            }
        }
    }
    anyhow::bail!("GitHub is not authenticated; run `codex-sync login` first")
}

pub fn login(client: &Client, client_id: &str, open_browser: bool) -> Result<Credential> {
    if client_id.trim().is_empty() {
        anyhow::bail!("GitHub App client ID is required for device login");
    }
    let device: DeviceCode = client
        .post("https://github.com/login/device/code")
        .header("Accept", "application/json")
        .form(&[("client_id", client_id)])
        .send()
        .context("start GitHub device authorization")?
        .error_for_status()
        .context("GitHub rejected device authorization")?
        .json()
        .context("parse GitHub device authorization response")?;

    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    write_device_prompt(&mut stdout, &device.verification_uri, &device.user_code)?;
    drop(stdout);
    if open_browser {
        let _ = open::that(&device.verification_uri);
    }

    let deadline = std::time::Instant::now() + Duration::from_secs(device.expires_in);
    let mut interval = device.interval.max(1);
    while std::time::Instant::now() < deadline {
        thread::sleep(Duration::from_secs(interval));
        let response: TokenResponse = client
            .post("https://github.com/login/oauth/access_token")
            .header("Accept", "application/json")
            .form(&[
                ("client_id", client_id),
                ("device_code", device.device_code.as_str()),
                ("grant_type", "urn:ietf:params:oauth:grant-type:device_code"),
            ])
            .send()
            .context("poll GitHub device authorization")?
            .error_for_status()
            .context("GitHub rejected device token request")?
            .json()
            .context("parse GitHub token response")?;
        if let Some(access_token) = response.access_token {
            let now = Utc::now();
            let credential = Credential {
                access_token,
                expires_at: response
                    .expires_in
                    .map(|seconds| now + ChronoDuration::seconds(seconds)),
                refresh_token: response.refresh_token,
                refresh_expires_at: response
                    .refresh_token_expires_in
                    .map(|seconds| now + ChronoDuration::seconds(seconds)),
            };
            store_credential(&credential)?;
            return Ok(credential);
        }
        match response.error.as_deref() {
            Some("authorization_pending") | None => {}
            Some("slow_down") => interval += 5,
            Some("expired_token") => anyhow::bail!("GitHub device code expired"),
            Some("access_denied") => anyhow::bail!("GitHub authorization was denied"),
            Some(error) => anyhow::bail!(
                "GitHub authorization failed: {}{}",
                error,
                response
                    .error_description
                    .map(|value| format!(": {value}"))
                    .unwrap_or_default()
            ),
        }
    }
    anyhow::bail!("GitHub device authorization timed out")
}

fn write_device_prompt(
    output: &mut impl Write,
    verification_uri: &str,
    user_code: &str,
) -> Result<()> {
    writeln!(output, "Open {verification_uri} and enter code {user_code}")
        .context("write GitHub device authorization instructions")?;
    output
        .flush()
        .context("flush GitHub device authorization instructions")
}

fn refresh(client: &Client, client_id: &str, previous: &Credential) -> Result<Credential> {
    let refresh_token = previous
        .refresh_token
        .as_deref()
        .context("GitHub credential has no refresh token")?;
    let response: TokenResponse = client
        .post("https://github.com/login/oauth/access_token")
        .header("Accept", "application/json")
        .form(&[
            ("client_id", client_id),
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh_token),
        ])
        .send()
        .context("refresh GitHub token")?
        .error_for_status()
        .context("GitHub rejected token refresh")?
        .json()
        .context("parse GitHub refresh response")?;
    if let Some(error) = response.error {
        anyhow::bail!("GitHub token refresh failed: {error}");
    }
    let now = Utc::now();
    Ok(Credential {
        access_token: response
            .access_token
            .context("GitHub refresh response omitted access_token")?,
        expires_at: response
            .expires_in
            .map(|seconds| now + ChronoDuration::seconds(seconds)),
        refresh_token: response
            .refresh_token
            .or_else(|| previous.refresh_token.clone()),
        refresh_expires_at: response
            .refresh_token_expires_in
            .map(|seconds| now + ChronoDuration::seconds(seconds))
            .or(previous.refresh_expires_at),
    })
}

fn keyring_entry() -> Result<Entry> {
    Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT).context("open OS credential store")
}

fn load_credential() -> Result<Option<Credential>> {
    let entry = keyring_entry()?;
    match entry.get_password() {
        Ok(value) => serde_json::from_str(&value)
            .context("parse GitHub credential from OS credential store")
            .map(Some),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error).context("read GitHub credential from OS credential store"),
    }
}

fn store_credential(credential: &Credential) -> Result<()> {
    let value = serde_json::to_string(credential).context("serialize GitHub credential")?;
    keyring_entry()?
        .set_password(&value)
        .context("store GitHub credential in OS credential store")
}

pub fn logout() -> Result<()> {
    let entry = keyring_entry()?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error).context("delete GitHub credential from OS credential store"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct FlushTrackingWriter {
        bytes: Vec<u8>,
        flushed: bool,
    }

    impl Write for FlushTrackingWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.bytes.extend_from_slice(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            self.flushed = true;
            Ok(())
        }
    }

    #[test]
    fn device_prompt_is_exact_and_flushed_before_polling() {
        let mut writer = FlushTrackingWriter::default();
        write_device_prompt(&mut writer, "https://github.com/login/device", "ABCD-EFGH").unwrap();

        assert!(writer.flushed);
        assert_eq!(
            String::from_utf8(writer.bytes).unwrap(),
            "Open https://github.com/login/device and enter code ABCD-EFGH\n"
        );
    }
}
