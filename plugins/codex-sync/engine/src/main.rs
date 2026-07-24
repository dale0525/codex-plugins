mod app;
mod auth;
mod config;
mod github;
mod model;
mod profiles;
mod reconcile;
mod storage;

use anyhow::Result;
use clap::{Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(
    name = "codex-sync",
    version,
    about = "Synchronize Codex configuration safely"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Configure the private GitHub repository and this device identity.
    Setup {
        #[arg(long)]
        repository: String,
        #[arg(long)]
        device: String,
        #[arg(long, default_value = "main")]
        git_ref: String,
        #[arg(long)]
        github_client_id: Option<String>,
        /// Replace an existing local setup after backing up its state.
        #[arg(long)]
        replace_existing: bool,
    },
    /// Authenticate to GitHub with the configured GitHub App device flow.
    Login {
        #[arg(long)]
        client_id: Option<String>,
        #[arg(long)]
        no_browser: bool,
    },
    /// Delete the locally stored GitHub credential.
    Logout,
    /// Fetch the private repository and create a reviewable synchronization plan.
    Sync {
        /// Discard unpublished edits in the local repository cache.
        #[arg(long)]
        discard_local: bool,
    },
    /// Apply a previously reviewed plan transactionally.
    Apply {
        plan_id: String,
        #[arg(long)]
        approve_high_risk: bool,
    },
    /// Show local synchronization state.
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Validate state, repository schema, secret policy, and Codex availability.
    Doctor,
    /// Restore the latest or a named pre-apply backup.
    Rollback {
        backup: Option<String>,
        #[arg(long)]
        approve: bool,
    },
    /// Publish reviewed edits in the local repository cache as one GitHub commit.
    Publish {
        #[arg(long, default_value = "Update synchronized Codex configuration")]
        message: String,
        #[arg(long)]
        approve: bool,
    },
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
            git_ref,
            github_client_id,
            replace_existing,
        } => app::setup(
            &repository,
            &device,
            &git_ref,
            github_client_id,
            replace_existing,
        ),
        Commands::Login {
            client_id,
            no_browser,
        } => app::login(client_id.as_deref(), !no_browser),
        Commands::Logout => app::logout(),
        Commands::Sync { discard_local } => app::sync(discard_local).map(|_| ()),
        Commands::Apply {
            plan_id,
            approve_high_risk,
        } => app::apply(&plan_id, approve_high_risk),
        Commands::Status { json } => app::status(json),
        Commands::Doctor => app::doctor(),
        Commands::Rollback { backup, approve } => app::rollback(backup.as_deref(), approve),
        Commands::Publish { message, approve } => app::publish(&message, approve),
    }
}
