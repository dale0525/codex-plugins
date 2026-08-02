#[allow(dead_code)]
pub fn compensate_operations(operations: &[RuntimeOperation]) -> Result<()> {
    compensate_operations_recorded(operations, None)
}

pub fn compensate_operations_recorded(
    operations: &[RuntimeOperation],
    mut recorder: Option<&mut OperationRecorder>,
) -> Result<()> {
    if let Some(log) = recorder.as_deref_mut() {
        // The caller must restore the core backup before invoking this
        // function.  The complete plan is durable before the first spawn.
        log.materialize_compensation_plan(operations)?;
        return execute_compensation_plan(log);
    }
    let mut failures = Vec::new();
    for operation in operations.iter().rev() {
        let action_id = operation.action_id.as_deref();
        if let Some(log) = recorder.as_deref() {
            // Intent and Running actions have no durable evidence that the
            // child completed. Never compensate an action that never ran (or
            // whose outcome is ambiguous after a restart).
            if let Some(id) = action_id {
                if !matches!(
                    log.status(id),
                    Some(ActionStatus::Completed | ActionStatus::Succeeded)
                ) {
                    continue;
                }
            } else {
                continue;
            }
        }
        let result: Result<()> = if operation.uninstall {
            match operation.previous.as_ref() {
                Some(receipt) => execute_receipt(receipt, false).map(|_| ()),
                None => Ok(()),
            }
        } else {
            let mut result = execute_receipt(&operation.receipt, true).map(|_| ());
            if result.is_ok() {
                if let Some(previous) = &operation.previous {
                    result = execute_receipt(previous, false).map(|_| ());
                }
            }
            result.map(|_| ())
        };
        if let Err(error) = result {
            failures.push(format!("{}: {error:#}", operation.plugin_id));
            // Without a recorder there is no durable checkpoint to update.
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        anyhow::bail!("runtime compensation failed: {}", failures.join("; "))
    }
}

/// Execute the already-materialized compensation plan.  A step is advanced to
/// Running durably before spawning the child; Running and ManualRequired are
/// never replayed after restart.  The original action records are untouched.
pub fn execute_compensation_plan(recorder: &mut OperationRecorder) -> Result<()> {
    let mut failures = Vec::new();
    let steps = recorder.log.compensation_steps.clone();
    for step in steps {
        match step.status {
            CompensationStatus::Completed => continue,
            CompensationStatus::Running | CompensationStatus::ManualRequired => {
                recorder.set_compensation_status(
                    &step.step_id,
                    CompensationStatus::ManualRequired,
                    Some(
                        "compensation outcome is ambiguous; verify runtime manually before retry"
                            .to_owned(),
                    ),
                )?;
                failures.push(format!(
                    "{}: compensation step is manual-required",
                    step.plugin_id
                ));
                continue;
            }
            CompensationStatus::Intent => {}
        }
        recorder.set_compensation_status(&step.step_id, CompensationStatus::Running, None)?;
        let result: Result<()> = if step.uninstall {
            match step.previous.as_ref() {
                Some(receipt) => execute_receipt(receipt, false).map(|_| ()),
                None => Ok(()),
            }
        } else {
            let mut result = execute_receipt(&step.receipt, true).map(|_| ());
            if result.is_ok() {
                if let Some(previous) = &step.previous {
                    result = execute_receipt(previous, false).map(|_| ());
                }
            }
            result
        };
        match result {
            Ok(()) => recorder.set_compensation_status(
                &step.step_id,
                CompensationStatus::Completed,
                None,
            )?,
            Err(error) => {
                recorder.set_compensation_status(
                    &step.step_id,
                    CompensationStatus::ManualRequired,
                    Some(format!("{error:#}")),
                )?;
                failures.push(format!("{}: {error:#}", step.plugin_id));
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        anyhow::bail!("runtime compensation failed: {}", failures.join("; "))
    }
}

#[allow(dead_code)]
pub fn prepare_rollback_runtime(
    current: &std::collections::BTreeMap<String, ProvisionReceipt>,
    target: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
) -> Result<()> {
    prepare_rollback_runtime_recorded(current, target, operations, None)
}

pub fn prepare_rollback_runtime_recorded(
    current: &std::collections::BTreeMap<String, ProvisionReceipt>,
    target: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
    recorder: Option<&mut OperationRecorder>,
) -> Result<()> {
    let ids: Vec<String> = current
        .keys()
        .filter(|id| target.get(*id) != current.get(*id))
        .cloned()
        .collect();
    run_uninstallers_recorded(&ids, current, operations, recorder).map(|_| ())
}
