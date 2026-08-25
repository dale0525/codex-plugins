mod app;
#[cfg(test)]
mod app_tests;
mod codex;
mod config;
mod migration;
mod model;
mod profiles;
mod storage;

use anyhow::Result;
use clap::{Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(
    name = "codex-sync",
    version,
    about = "Synchronize Codex configuration through Git"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Bind this device to a Git repository.
    Setup {
        #[arg(long)]
        repository: Option<String>,
        #[arg(long)]
        device: Option<String>,
        #[arg(long, default_value = "main")]
        branch: String,
    },
    /// Fetch the remote branch and converge local Codex state.
    Pull {
        #[arg(long)]
        dry_run: bool,
    },
    /// Capture this device and commit it to the remote branch.
    Push {
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        message: Option<String>,
    },
    /// Show the binding and convergence state.
    Status,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("error: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Setup {
            repository,
            device,
            branch,
        } => app::setup(repository.as_deref(), device.as_deref(), &branch),
        Commands::Pull { dry_run } => app::pull(dry_run),
        Commands::Push { dry_run, message } => app::push(dry_run, message.as_deref()),
        Commands::Status => app::status(),
    }
}
