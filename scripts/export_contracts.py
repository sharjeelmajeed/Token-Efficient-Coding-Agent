import json
import os
import secrets
import tempfile
from pathlib import Path

from token_agent.api import create_app
from token_agent.config import Settings, UserConfig
from token_agent.models import Event

root = Path(__file__).resolve().parents[1]
variable = "AGENT_SCHEMA_ONLY_TOKEN"
previous = os.environ.get(variable)
os.environ[variable] = secrets.token_urlsafe(40)
try:
    with tempfile.TemporaryDirectory(prefix="agent-contract-") as directory:
        settings = Settings(
            database=str(Path(directory) / "schema.sqlite3"),
            users=[UserConfig(id="schema", token_env=variable, repo_roots=[directory])],
        )
        app = create_app(settings)
        (root / "docs" / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
        (root / "docs" / "event.schema.json").write_text(
            json.dumps(Event.model_json_schema(), indent=2) + "\n"
        )
        app.state.store.close()  # release the database before the folder is removed
finally:
    if previous is None:
        os.environ.pop(variable, None)
    else:
        os.environ[variable] = previous
print("Client contracts exported.")
