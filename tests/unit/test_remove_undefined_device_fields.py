from unittest.mock import AsyncMock

import pytest

from scripts import remove_undefined_device_fields as script


@pytest.mark.parametrize("argv", [
    [], ["--apply"], ["--all", "region"], ["legacy", "typeId"],
    ["--fields", "legacy"], ["--deviceType", "default"],
    ["--all-fields"], ["--all-device-types"],
    ["--fields", "legacy", "--all-fields", "--deviceType", "default"],
    ["--fields", "legacy", "--deviceType", "default", "--all-device-types"],
    ["--all", "--fields", "legacy"], ["--all", "--all-fields"],
    ["--all", "--deviceType", "default"], ["--all", "--all-device-types"],
    ["--fields", " ", "--deviceType", "default"],
    ["--fields", "", "--deviceType", "default"],
    ["--fields", "legacy", "--deviceType", " "],
    ["--fields", "legacy", "--deviceType", ""],
])
def test_invalid_selection_cannot_run(argv):
    with pytest.raises(SystemExit) as exc:
        script.parse_args(argv)
    assert exc.value.code == 2


def test_named_fields_are_dry_run_by_default():
    args = script.parse_args(["--fields", "legacy", "typeId", "--deviceType", "default"])
    assert args.fields == ["legacy", "typeId"]
    assert not args.all
    assert not args.all_fields
    assert not args.all_device_types
    assert args.device_type == "default"
    assert not args.apply


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("answer", ["", "all", "yes", None])
@pytest.mark.parametrize("selection", [
    ["--all"], ["--all-fields", "--deviceType", "default"],
    ["--fields", "legacy", "--all-device-types"],
    ["--all-fields", "--all-device-types"],
])
async def test_all_requires_exact_confirmation_before_database_access(monkeypatch, apply, answer, selection):
    run = AsyncMock()
    monkeypatch.setattr(script, "run", run)

    def respond(prompt):
        if answer is None:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", respond)
    with pytest.raises(SystemExit, match="cancelled"):
        await script.main(selection + (["--apply"] if apply else []))
    run.assert_not_awaited()


@pytest.mark.parametrize("apply", [False, True])
async def test_confirmed_all_runs_in_requested_mode(monkeypatch, capsys, apply):
    run = AsyncMock(return_value=script.CleanupResult(2, {"legacy": 2}))
    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr(script, "engine", AsyncMock())
    monkeypatch.setattr("builtins.input", lambda prompt: "ALL")
    await script.main(["--all"] + (["--apply"] if apply else []))
    run.assert_awaited_once_with([], True, apply, device_type=None, all_device_types=True)
    output = capsys.readouterr().out
    assert "2 field values across 2 devices" in output
    assert ("Applied" if apply else "Dry run (rolled back)") in output


async def test_no_arguments_never_runs_cleanup(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(script, "run", run)
    with pytest.raises(SystemExit):
        await script.main([])
    run.assert_not_awaited()


@pytest.mark.parametrize("selection,fields,all_fields,device_type,all_types", [
    (["--all"], [], True, None, True),
    (["--all-fields", "--all-device-types"], [], True, None, True),
    (["--all-fields", "--deviceType", "default"], [], True, "default", False),
    (["--fields", "legacy", "--all-device-types"], ["legacy"], False, None, True),
])
async def test_explicit_selection_reaches_cleanup(
    monkeypatch, capsys, selection, fields, all_fields, device_type, all_types,
):
    run = AsyncMock(return_value=script.CleanupResult(0, {}))
    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr(script, "engine", AsyncMock())
    monkeypatch.setattr("builtins.input", lambda prompt: "ALL")
    await script.main(selection)
    run.assert_awaited_once_with(
        fields, all_fields, False, device_type=device_type, all_device_types=all_types,
    )
    assert "Selection:" in capsys.readouterr().out


async def test_named_fields_and_named_type_do_not_require_broad_confirmation(monkeypatch, capsys):
    run = AsyncMock(return_value=script.CleanupResult(1, {"test": 1}))
    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr(script, "engine", AsyncMock())
    def unexpected_input(prompt):
        raise AssertionError("Narrow selection must not ask for broad confirmation")

    monkeypatch.setattr("builtins.input", unexpected_input)
    await script.main(["--fields", "test", "--deviceType", "default", "--apply"])
    run.assert_awaited_once_with(
        ["test"], False, True, device_type="default", all_device_types=False,
    )
    output = capsys.readouterr().out
    assert "fields=['test']; device types='default'" in output
    assert "1 field values across 1 devices" in output
