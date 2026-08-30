import json
import os

from src.packages.shared.models import WorkflowRunConclusion, WorkflowRunEvent

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../fixtures/webhook_workflow_run_failed.json",
)


def test_workflow_run_event_normalization() -> None:
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    wf_data = data["workflow_run"]
    repo_data = data["repository"]

    event = WorkflowRunEvent(
        event_type="workflow_run",
        action=data["action"],
        repository_owner=repo_data["owner"]["login"],
        repository_name=repo_data["name"],
        run_id=wf_data["id"],
        workflow_name=wf_data["name"],
        head_branch=wf_data["head_branch"],
        head_sha=wf_data["head_sha"],
        run_url=wf_data["html_url"],
        conclusion=WorkflowRunConclusion(wf_data["conclusion"]),
        sender=data["sender"]["login"],
    )

    assert event.repository_owner == "crlabs-ai"
    assert event.repository_name == "akesis"
    assert event.run_id == 987654321
    assert event.conclusion == WorkflowRunConclusion.FAILURE
    assert event.head_branch == "feat/payment-processor"
