from qka_shared.acl import SourceAclState, evaluate, evaluate_needles
from qka_shared.models import SynologyFileInventory


def test_missing_acl_denies() -> None:
    inventory = SynologyFileInventory(
        acl_capture_status=0,
        acl_snapshot_json=None,
    )
    assert evaluate(inventory, "eduard") is SourceAclState.MISSING


def test_allow_principal_matches() -> None:
    inventory = SynologyFileInventory(
        acl_capture_status=1,
        acl_snapshot_json='[{"Identity":"S-1-5-21-4108582196-371218740-2140778505-3104","Rights":"Read","AccessControlType":"Allow"}]',
    )
    assert evaluate(inventory, "S-1-5-21-4108582196") is SourceAclState.ALLOWED
    assert evaluate(inventory, "someone-else") is SourceAclState.DENIED
    assert evaluate_needles(inventory, ["someone-else", "S-1-5-21-4108582196-371218740-2140778505-3104"]) is SourceAclState.ALLOWED
    assert evaluate_needles(inventory, []) is SourceAclState.MISSING
