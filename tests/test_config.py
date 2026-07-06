"""Config .env loading: file values fill the environment, real env wins."""

from brutus.config import Config


def test_dotenv_fills_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nGITHUB_TOKEN=abc\nBRUTUS_LLM_CMD="python glm.py"\n')
    monkeypatch.setenv("BRUTUS_ENV_FILE", str(env))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("BRUTUS_LLM_CMD", raising=False)

    cfg = Config.load()
    assert cfg.github_token == "abc"
    assert cfg.llm_cmd == "python glm.py"  # quotes stripped


def test_real_env_overrides_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GITHUB_TOKEN=fromfile\n")
    monkeypatch.setenv("BRUTUS_ENV_FILE", str(env))
    monkeypatch.setenv("GITHUB_TOKEN", "fromenv")

    assert Config.load().github_token == "fromenv"
