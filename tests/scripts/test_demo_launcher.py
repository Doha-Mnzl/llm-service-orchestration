"""Sûreté du lanceur de démonstration : dossier d'exécution privé, jeton jamais exposé (revue sécurité)."""

import os
import stat

import pytest

from scripts import partie3_demo as demo

pytestmark = pytest.mark.skipif(os.name == "nt", reason="droits POSIX")


def test_run_dir_is_private_and_not_in_shared_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    rd = demo.secure_run_dir(28000)
    assert rd.is_relative_to(tmp_path) and not str(rd).startswith("/tmp/lso-partie3-demo")
    for d in (rd, rd.parent):
        assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700


def test_run_dir_refuses_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "ailleurs").mkdir(mode=0o700)
    (tmp_path / "lso-partie3-demo").mkdir(mode=0o700)
    (tmp_path / "lso-partie3-demo" / "28000").symlink_to(tmp_path / "ailleurs")
    with pytest.raises(SystemExit):
        demo.secure_run_dir(28000)


def test_run_dir_refuses_a_directory_open_to_others(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    d = tmp_path / "lso-partie3-demo"
    d.mkdir()
    d.chmod(0o777)
    with pytest.raises(SystemExit):
        demo.secure_run_dir(28000)


def test_token_is_created_private_without_following_a_planted_symlink(tmp_path):
    victim = tmp_path / "fichier-de-la-victime"
    victim.write_text("contenu important")
    (tmp_path / "catalog_token").symlink_to(victim)
    demo.write_private(tmp_path / "catalog_token", "jeton-secret", exclusive=True)
    assert victim.read_text() == "contenu important"
    assert (tmp_path / "catalog_token").read_text() == "jeton-secret"
    assert stat.S_IMODE(os.lstat(tmp_path / "catalog_token").st_mode) == 0o600


def test_logs_never_follow_a_planted_symlink(tmp_path):
    victim = tmp_path / "authorized_keys"
    victim.write_text("clé")
    (tmp_path / "sales.log").symlink_to(victim)
    with pytest.raises(OSError):
        demo.open_private_log(tmp_path / "sales.log")
    assert victim.read_text() == "clé"


def test_service_environment_cannot_be_overridden_by_the_shell(monkeypatch):
    monkeypatch.setenv("CATALOG_DATABASE_URL", "postgresql://lso:lso@localhost:5433/lso_dev")
    monkeypatch.setenv("DECISIONS_LEGACY_ORCHESTRATOR", "true")
    env = demo.service_env("dbname=lso_demo_test", token="t" * 32)
    assert all(env[k] == "dbname=lso_demo_test" for k in
               ("DATABASE_URL", "SALES_DATABASE_URL", "INVENTORY_DATABASE_URL", "CATALOG_DATABASE_URL"))
    assert env["DECISIONS_LEGACY_ORCHESTRATOR"] == "false" and env["CATALOG_SERVICE_TOKENS"] == "demo:" + "t" * 32


@pytest.mark.parametrize("pg_url", ["postgresql://lso:lso@localhost:5433/lso_dev?application_name=x",
                                    "postgresql://lso:lso@localhost:5433/lso_dev"])
def test_database_name_is_checked_on_the_final_connection_string(pg_url):
    conninfo = demo.database_conninfo(pg_url, "lso_demo_test")
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(conninfo)["dbname"] == "lso_demo_test"
    with pytest.raises(SystemExit):
        demo.database_conninfo(pg_url, "lso_dev")


def test_stop_only_kills_processes_that_belong_to_the_stack():
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--port", "28999"])
    try:
        import time
        deadline = time.monotonic() + 5  # attendre que l'enfant ait remplacé la ligne de commande héritée
        while not demo.belongs(proc.pid, ["--port", "28999"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert demo.belongs(proc.pid, ["--port", "28999"]) is True
        assert demo.belongs(proc.pid, ["--port", "28001"]) is False
        assert demo.belongs(999999, ["--port", "28999"]) is False
    finally:
        proc.kill()


def test_start_refuses_a_missing_orchestrator_folder_before_spawning(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    args = type("A", (), {"port_base": 27000, "pg_url": "postgresql://lso:lso@localhost:5433",
                          "database": "lso_demo_test", "orchestrator_src": str(tmp_path / "absent"),
                          "legacy_catalog": False})()
    with pytest.raises(SystemExit):
        demo.start(args)
    assert not (demo.run_dir(27000) / "pids.json").exists()


def test_database_connection_never_appears_in_process_arguments():
    for argv in demo.data_commands():
        assert "--database-url" not in argv and not any("postgres" in a or "password" in a for a in argv)


def _state(tmp_path, monkeypatch, pid, args):
    import json
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    rd = demo.secure_run_dir(27100)
    (rd / "logs").mkdir(mode=0o700)
    state = {"pids": {"sales": pid}, "ports": {"sales": 27999}, "database": "dbname=lso_demo_test",
             "specs": {"sales": {"args": args, "env": {}, "cwd": str(tmp_path)}}}
    demo.write_private(rd / "pids.json", json.dumps(state))
    return rd, type("A", (), {"port_base": 27100, "service": "sales"})()


def test_start_service_refuses_while_the_tracked_instance_is_alive(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--port", "27999"])
    try:
        import time
        deadline = time.monotonic() + 5
        while not demo.belongs(live.pid, ["--port", "27999"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        rd, a = _state(tmp_path, monkeypatch, live.pid, [sys.executable, "-c", "pass"])
        with pytest.raises(SystemExit):
            demo.start_service(a)
        assert json.loads((rd / "pids.json").read_text())["pids"]["sales"] == live.pid
    finally:
        live.kill()


def test_start_service_never_publishes_the_pid_of_a_child_that_died(tmp_path, monkeypatch):
    import json
    import sys
    rd, a = _state(tmp_path, monkeypatch, 0, [sys.executable, "-c", "import sys; sys.exit(3)", "--port", "27999"])
    with pytest.raises(SystemExit):
        demo.start_service(a)
    assert json.loads((rd / "pids.json").read_text())["pids"]["sales"] == 0


def test_service_commands_reject_unknown_names_and_missing_stack(tmp_path, monkeypatch, capsys):
    import sys
    rd, a = _state(tmp_path, monkeypatch, 0, [sys.executable, "-c", "pass"])
    a.service = "inventroy"
    with pytest.raises(SystemExit):
        demo.stop_service(a)
    assert "inventroy" not in (rd / "pids.json").read_text()
    (rd / "pids.json").unlink()
    a.service = "sales"
    with pytest.raises(SystemExit):
        demo.start_service(a)


@pytest.mark.parametrize("dead_child", [True, False], ids=["foreign-health", "timeout"])
def test_failed_stack_start_rolls_back_without_publishing(tmp_path, monkeypatch, dead_child):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    args = SimpleNamespace(port_base=27700, orchestrator_src=None, legacy_catalog=False,
                           pg_url="", database="private_test")
    monkeypatch.setattr(demo.socket, "socket", MagicMock())
    monkeypatch.setattr(demo, "prepare_database", lambda *_: "dbname=private_test")
    children = {}

    def spawn(name, *unused):
        code = 1 if dead_child and name == "sales" else None
        proc = SimpleNamespace(pid=100 + len(children), poll=lambda: code)
        children[name] = proc
        return proc

    monkeypatch.setattr(demo, "_spawn", spawn)
    monkeypatch.setattr(demo, "_healthy", lambda url: dead_child)
    monkeypatch.setattr(demo.time, "monotonic", iter([0, 41]).__next__)
    killed = []
    monkeypatch.setattr(demo, "_kill", killed.append)
    with pytest.raises(SystemExit, match="arrêt|Délai"):
        demo.start(args)
    assert set(killed) == {proc.pid for proc in children.values() if proc.poll() is None}
    assert not (demo.run_dir(args.port_base) / "pids.json").exists()


def test_status_rejects_foreign_healthy_endpoint(tmp_path, monkeypatch, capsys):
    _, args = _state(tmp_path, monkeypatch, 12345, [])
    monkeypatch.setattr(demo, "belongs", lambda *_: False)
    monkeypatch.setattr(demo, "_healthy", lambda *_: True)
    demo.status(args)
    assert "INDISPONIBLE" in capsys.readouterr().out


def test_occupied_port_refused_before_database_setup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    args = SimpleNamespace(port_base=27700, orchestrator_src=None, legacy_catalog=False)
    probe = MagicMock()
    probe.return_value.__enter__.return_value.bind.side_effect = OSError("address in use")
    monkeypatch.setattr(demo.socket, "socket", probe)
    with pytest.raises(SystemExit, match="sales : port 27701 indisponible"):
        demo.start(args)
    assert not (demo.run_dir(args.port_base) / "pids.json").exists()
