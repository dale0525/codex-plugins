//! Read-only Codex provider detection and effective output-limit policy.

use crate::control::settings::{Tier, ToolBudgetLevel, ToolBudgetPreferences, ToolBudgets};
use std::path::Path;
use toml_edit::Item;

/// Host-side output ceiling used when Codex must compact locally.
pub const GUARDED_HOST_LIMIT: i64 = 10_000;
/// FastCtx response budget kept below the Guarded host ceiling.
pub const GUARDED_FASTCTX_BUDGET: usize = 9_000;

/// Codex compaction capability inferred from the selected provider.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompactionSupport {
    /// Codex routes compaction to OpenAI or Azure.
    Remote,
    /// Codex performs inline local compaction for this provider.
    Local,
    /// The selected provider could not be resolved safely from the visible configuration.
    Unknown,
}

/// Read-only classification of the provider visible in one Codex configuration file.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderDetection {
    /// Selected provider identifier when it could be read.
    pub provider_id: Option<String>,
    /// Friendly provider name when it could be resolved.
    pub provider_name: Option<String>,
    /// Whether Codex supports remote compaction for the resolved provider.
    pub support: CompactionSupport,
    /// Stable English explanation suitable for Status diagnostics.
    pub detail: String,
}

impl ProviderDetection {
    fn resolved(
        provider_id: impl Into<String>,
        provider_name: impl Into<String>,
        support: CompactionSupport,
    ) -> Self {
        let provider_id = provider_id.into();
        let provider_name = provider_name.into();
        let detail = match support {
            CompactionSupport::Remote => format!(
                "Codex provider {provider_id} ({provider_name}) supports remote compaction."
            ),
            CompactionSupport::Local => format!(
                "Codex provider {provider_id} ({provider_name}) uses local inline compaction."
            ),
            CompactionSupport::Unknown => unreachable!("resolved providers are never unknown"),
        };
        Self {
            provider_id: Some(provider_id),
            provider_name: Some(provider_name),
            support,
            detail,
        }
    }

    fn unknown(provider_id: Option<String>, detail: impl Into<String>) -> Self {
        Self {
            provider_id,
            provider_name: None,
            support: CompactionSupport::Unknown,
            detail: detail.into(),
        }
    }

    /// Whether the Guarded output policy should be active when protection is enabled.
    pub const fn requires_guard(&self) -> bool {
        matches!(self.support, CompactionSupport::Local)
    }
}

/// Effective output mode for one provider/configuration snapshot.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EffectiveOutputMode {
    /// The user's selected tier is effective.
    SelectedTier,
    /// The provider requires local compaction, so Guarded ceilings are effective.
    Guarded,
}

/// Concrete output limits consumed by Apply and by one runtime session.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EffectiveOutput {
    /// User preference retained independently from the environment-derived mode.
    pub selected_tier: Tier,
    /// Environment-derived effective mode.
    pub mode: EffectiveOutputMode,
    /// Host token limit written by Apply.
    pub host_limit: i64,
    /// Global FastCtx response budget.
    pub fastctx_budget: usize,
    /// Concrete per-tool shares, including defaults for the effective mode.
    pub tool_budgets: ToolBudgets,
}

/// Reads and classifies the visible Codex config without touching credentials.
pub fn detect_path(path: &Path) -> ProviderDetection {
    match std::fs::read(path) {
        Ok(bytes) => detect_bytes(Some(&bytes)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => detect_bytes(None),
        Err(error) => ProviderDetection::unknown(
            None,
            format!(
                "Cannot read Codex config {} while detecting the model provider: {error}",
                crate::paths::display_path(path)
            ),
        ),
    }
}

/// Classifies a Codex config snapshot; absence means Codex's built-in OpenAI default.
pub fn detect_bytes(config: Option<&[u8]>) -> ProviderDetection {
    let Some(config) = config else {
        return ProviderDetection::resolved("openai", "OpenAI", CompactionSupport::Remote);
    };
    let source = match std::str::from_utf8(config) {
        Ok(source) => source,
        Err(error) => {
            return ProviderDetection::unknown(
                None,
                format!(
                    "Codex config is not valid UTF-8 ({error}); provider detection was skipped."
                ),
            );
        }
    };
    let document = match source.parse::<toml_edit::DocumentMut>() {
        Ok(document) => document,
        Err(error) => {
            return ProviderDetection::unknown(
                None,
                format!("Codex config could not be parsed for provider detection: {error}"),
            );
        }
    };
    let provider_id = match document.get("model_provider") {
        None => "openai".to_string(),
        Some(item) => match item.as_str().filter(|value| !value.is_empty()) {
            Some(value) => value.to_string(),
            None => {
                return ProviderDetection::unknown(
                    None,
                    "Codex config key model_provider is not a non-empty string; provider detection was skipped.",
                );
            }
        },
    };

    match provider_id.as_str() {
        "openai" => {
            return ProviderDetection::resolved(provider_id, "OpenAI", CompactionSupport::Remote);
        }
        "amazon-bedrock" => {
            return ProviderDetection::resolved(
                provider_id,
                "Amazon Bedrock",
                CompactionSupport::Local,
            );
        }
        "ollama" | "lmstudio" => {
            return ProviderDetection::resolved(provider_id, "gpt-oss", CompactionSupport::Local);
        }
        _ => {}
    }

    let Some(providers) = document
        .get("model_providers")
        .and_then(Item::as_table_like)
    else {
        return ProviderDetection::unknown(
            Some(provider_id.clone()),
            format!(
                "Codex model_provider {provider_id} has no matching model_providers.{provider_id} definition; output protection was not tightened."
            ),
        );
    };
    let Some(provider) = providers.get(&provider_id).and_then(Item::as_table_like) else {
        return ProviderDetection::unknown(
            Some(provider_id.clone()),
            format!(
                "Codex model_provider {provider_id} has no matching model_providers.{provider_id} definition; output protection was not tightened."
            ),
        );
    };
    let Some(name) = provider
        .get("name")
        .and_then(Item::as_str)
        .filter(|name| !name.is_empty())
    else {
        return ProviderDetection::unknown(
            Some(provider_id.clone()),
            format!(
                "Codex provider model_providers.{provider_id}.name is missing or invalid; output protection was not tightened."
            ),
        );
    };
    let base_url = match provider.get("base_url") {
        None => None,
        Some(item) => match item.as_str() {
            Some(value) => Some(value),
            None => {
                return ProviderDetection::unknown(
                    Some(provider_id.clone()),
                    format!(
                        "Codex provider model_providers.{provider_id}.base_url is not a string; output protection was not tightened."
                    ),
                );
            }
        },
    };
    let support = if name == "OpenAI" || is_azure_responses_provider(name, base_url) {
        CompactionSupport::Remote
    } else {
        CompactionSupport::Local
    };
    ProviderDetection::resolved(provider_id, name, support)
}

/// Resolves the user's preference against the provider-derived protection state.
pub fn effective_output(
    selected_tier: Tier,
    preferences: ToolBudgetPreferences,
    guard_enabled: bool,
    detection: &ProviderDetection,
) -> EffectiveOutput {
    if guard_enabled && detection.requires_guard() {
        let defaults = ToolBudgets {
            read: ToolBudgetLevel::Inherit,
            grep: ToolBudgetLevel::Inherit,
            glob: ToolBudgetLevel::Inherit,
            run: ToolBudgetLevel::Inherit,
            job_output: ToolBudgetLevel::Inherit,
        };
        EffectiveOutput {
            selected_tier,
            mode: EffectiveOutputMode::Guarded,
            host_limit: GUARDED_HOST_LIMIT,
            fastctx_budget: GUARDED_FASTCTX_BUDGET,
            tool_budgets: ToolBudgets {
                read: preferences.read.unwrap_or(defaults.read),
                grep: preferences.grep.unwrap_or(defaults.grep),
                glob: preferences.glob.unwrap_or(defaults.glob),
                run: preferences.run.unwrap_or(defaults.run),
                job_output: preferences.job_output.unwrap_or(defaults.job_output),
            },
        }
    } else {
        EffectiveOutput {
            selected_tier,
            mode: EffectiveOutputMode::SelectedTier,
            host_limit: selected_tier.host_limit(),
            fastctx_budget: selected_tier.fastctx_budget(),
            tool_budgets: preferences.resolve(selected_tier),
        }
    }
}

// Keep this byte-for-byte aligned with Codex's `is_azure_responses_provider`. The six markers are
// the current upstream set as of 2026-08-01; a mismatch changes which compaction implementation
// Codex selects and therefore changes the reason this guard exists.
fn is_azure_responses_provider(name: &str, base_url: Option<&str>) -> bool {
    if name.eq_ignore_ascii_case("azure") {
        return true;
    }
    let Some(base_url) = base_url else {
        return false;
    };
    let base_url = base_url.to_ascii_lowercase();
    [
        "openai.azure.",
        "cognitiveservices.azure.",
        "aoai.azure.",
        "azure-api.",
        "azurefd.",
        "windows.net/openai",
    ]
    .iter()
    .any(|marker| base_url.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::{
        CompactionSupport, EffectiveOutputMode, GUARDED_FASTCTX_BUDGET, GUARDED_HOST_LIMIT,
        detect_bytes, effective_output,
    };
    use crate::control::settings::{Tier, ToolBudgetLevel, ToolBudgetPreferences};

    #[test]
    fn provider_detection_matches_codex_remote_compaction_rules() {
        let cases = [
            (None, CompactionSupport::Remote, Some("openai")),
            (
                Some(b"theme = 'dark'\n".as_slice()),
                CompactionSupport::Remote,
                Some("openai"),
            ),
            (
                Some(b"model_provider = 'openai'\n".as_slice()),
                CompactionSupport::Remote,
                Some("openai"),
            ),
            (
                Some(b"model_provider = 'amazon-bedrock'\n".as_slice()),
                CompactionSupport::Local,
                Some("amazon-bedrock"),
            ),
            (
                Some(b"model_provider = 'ollama'\n".as_slice()),
                CompactionSupport::Local,
                Some("ollama"),
            ),
            (
                Some(b"model_provider = 'lmstudio'\n".as_slice()),
                CompactionSupport::Local,
                Some("lmstudio"),
            ),
            (
                Some(
                    b"model_provider='custom'\n[model_providers.custom]\nname='OpenAI'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("custom"),
            ),
            (
                Some(
                    b"model_provider='custom'\n[model_providers.custom]\nname='openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Local,
                Some("custom"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='aZuRe'\nbase_url='https://example.test'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://EXAMPLE.OPENAI.AZURE.COM/openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://host.cognitiveservices.azure.cn/openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://host.aoai.azure.com/openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://gateway.azure-api.net/openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://edge.azurefd.net/'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://host.windows.net/openai/deployments/x'\n"
                        .as_slice(),
                ),
                CompactionSupport::Remote,
                Some("az"),
            ),
            (
                Some(
                    b"model_provider='az'\n[model_providers.az]\nname='proxy'\nbase_url='https://host.azurewebsites.net/openai'\n"
                        .as_slice(),
                ),
                CompactionSupport::Local,
                Some("az"),
            ),
        ];
        for (source, support, provider_id) in cases {
            let detected = detect_bytes(source);
            assert_eq!(detected.support, support, "{source:?}: {detected:?}");
            assert_eq!(detected.provider_id.as_deref(), provider_id);
        }
    }

    #[test]
    fn malformed_or_unresolved_providers_never_tighten_silently() {
        for source in [
            b"[broken".as_slice(),
            b"model_provider = 1\n".as_slice(),
            b"model_provider = 'missing'\n".as_slice(),
            b"model_provider='custom'\n[model_providers.custom]\nbase_url='https://example.test'\n"
                .as_slice(),
            b"model_provider='custom'\n[model_providers.custom]\nname='Third Party'\nbase_url=1\n"
                .as_slice(),
        ] {
            let detected = detect_bytes(Some(source));
            assert_eq!(detected.support, CompactionSupport::Unknown, "{detected:?}");
            assert!(!detected.detail.is_empty());
        }
    }

    #[test]
    fn guarded_output_preserves_the_tier_preference_and_explicit_shares() {
        let detection = detect_bytes(Some(
            b"model_provider='custom'\n[model_providers.custom]\nname='Third Party'\n",
        ));
        let preferences = ToolBudgetPreferences {
            grep: Some(ToolBudgetLevel::Percent(37)),
            ..ToolBudgetPreferences::default()
        };
        let guarded = effective_output(Tier::High, preferences, true, &detection);
        assert_eq!(guarded.selected_tier, Tier::High);
        assert_eq!(guarded.mode, EffectiveOutputMode::Guarded);
        assert_eq!(guarded.host_limit, GUARDED_HOST_LIMIT);
        assert_eq!(guarded.fastctx_budget, GUARDED_FASTCTX_BUDGET);
        assert_eq!(guarded.tool_budgets.read, ToolBudgetLevel::Inherit);
        assert_eq!(guarded.tool_budgets.grep, ToolBudgetLevel::Percent(37));
        assert_eq!(guarded.tool_budgets.glob, ToolBudgetLevel::Inherit);

        let disabled = effective_output(Tier::High, preferences, false, &detection);
        assert_eq!(disabled.mode, EffectiveOutputMode::SelectedTier);
        assert_eq!(disabled.host_limit, Tier::High.host_limit());
        assert_eq!(
            disabled.tool_budgets.glob,
            Tier::High.default_budgets().glob
        );
    }
}
