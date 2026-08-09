// Modified by dale0525/codex-plugins for the compact managed-marker contract.
use fastctx::control::agents;
use fastctx::control::codex_config::{self, ExpectedConfig};
use fastctx::control::settings::{Tier, ToolBudgetLevel, ToolBudgets};

#[test]
fn micro_edit_golden_preserves_every_unowned_byte_and_writes_the_exact_private_shape() {
    let original = concat!(
        "# heading\n",
        "custom = 'value'\n",
        "\n",
        "tool_output_token_limit = 9000 # shared\n",
        "\n",
        "[mcp_servers.other]\n",
        "command = 'other'\n",
        "\n",
        "[features.code_mode]\n",
        "direct_only_tool_namespaces = [ 'alpha', 'omega' ]\n",
    );
    let expected = concat!(
        "# heading\n",
        "custom = 'value'\n",
        "\n",
        "tool_output_token_limit = 60000 # shared\n",
        "\n",
        "[mcp_servers.other]\n",
        "command = 'other'\n",
        "\n",
        "[mcp_servers.fastctx]\n",
        "command = \"C:/Users/test/.fastctx/bin/fastctx.exe\"\n",
        "args = [\"serve\"]\n",
        "startup_timeout_sec = 120\n",
        "tool_timeout_sec = 300\n",
        "\n",
        "[mcp_servers.fastctx.env]\n",
        "FASTCTX_TOKEN_BUDGET = \"54000\"\n",
        "FASTCTX_GREP_TOKEN_BUDGET = \"27000\"\n",
        "FASTCTX_GLOB_TOKEN_BUDGET = \"13500\"\n",
        "\n",
        "[features.code_mode]\n",
        "direct_only_tool_namespaces = [ 'alpha', 'omega', \"mcp__fastctx\" ]\n",
    );
    let edit = codex_config::apply(
        original.as_bytes(),
        &ExpectedConfig {
            command: "C:/Users/test/.fastctx/bin/fastctx.exe".to_string(),
            tier: Tier::Standard,
            host_limit: Tier::Standard.host_limit(),
            fastctx_budget: Tier::Standard.fastctx_budget(),
            tool_budgets: ToolBudgets {
                read: ToolBudgetLevel::Inherit,
                grep: ToolBudgetLevel::Percent(50),
                glob: ToolBudgetLevel::Percent(25),
                run: ToolBudgetLevel::Inherit,
                job_output: ToolBudgetLevel::Inherit,
            },
            fastshell_enabled: false,
        },
    )
    .unwrap();
    assert_eq!(edit.bytes, expected.as_bytes());
    assert_eq!(edit.conflict.unwrap().current, 9_000);
}

#[test]
fn agents_golden_appends_the_exact_contract_after_one_blank_line() {
    let original = "# User rules\n\nKeep exact.\n";
    let expected = concat!(
        "# User rules\n\nKeep exact.\n\n",
        "<!-- fastctx:begin -->\n",
        "- Prefer FastCtx tools for local file inspection and mechanical batch replacement when available; follow their tool schemas for invocation details.\n",
        "- When FastCtx shell tools are enabled, prefer them for non-interactive Bash commands and background jobs.\n",
        "<!-- fastctx:end -->\n",
    );
    assert_eq!(
        agents::apply_section(original.as_bytes()).unwrap(),
        expected.as_bytes()
    );
}

#[test]
fn malformed_toml_and_ambiguous_agents_markers_fail_before_producing_bytes() {
    let expected = ExpectedConfig {
        command: "/home/test/.fastctx/bin/fastctx".to_string(),
        tier: Tier::Standard,
        host_limit: Tier::Standard.host_limit(),
        fastctx_budget: Tier::Standard.fastctx_budget(),
        tool_budgets: ToolBudgets::default(),
        fastshell_enabled: false,
    };
    let toml_error = codex_config::apply(b"[broken", &expected).unwrap_err();
    assert!(toml_error.contains("Repair it manually"));
    let agents_error = agents::apply_section(
        b"<!-- fastctx:begin -->\n<!-- fastctx:begin -->\n<!-- fastctx:end -->",
    )
    .unwrap_err();
    assert!(agents_error.contains("duplicate or unmatched"));
}
