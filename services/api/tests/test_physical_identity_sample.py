"""Only the platform's host mount supplies the runner's physical identity."""

import pytest


def test_runner_reads_normalized_host_machine_id_without_exposing_it(tmp_path, monkeypatch):
    from app.runner_agent import agent

    source = tmp_path / "host-machine-id"
    monkeypatch.setattr(agent, "HOST_MACHINE_ID", source, raising=False)
    source.write_text("00000000000000000000000000000001\n")
    assert (
        agent.physical_host_id()
        == "e8b11c5baa73b66ce3340c344f82048c88cd806bcecfcc6816a89e8d6207a5ac"
    )


@pytest.mark.parametrize("raw", [None, "", "not-a-machine", "A" * 32, "0" * 31])
def test_runner_never_invents_a_machine_identity_for_missing_or_invalid_mount(
    raw,
    tmp_path,
    monkeypatch,
):
    from app.runner_agent import agent

    source = tmp_path / "host-machine-id"
    monkeypatch.setattr(agent, "HOST_MACHINE_ID", source, raising=False)
    if raw is not None:
        source.write_text(raw)
    assert agent.physical_host_id() is None
