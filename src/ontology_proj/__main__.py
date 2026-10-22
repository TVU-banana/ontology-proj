import argparse
from pathlib import Path

from pydantic import ValidationError
import uvicorn

from ontology_proj.bootstrap import create_app
from ontology_proj.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Local single-process MVP application")
    parser.add_argument("--env-file", default=None, help="Explicit settings file; none loaded by default")
    args = parser.parse_args()
    if args.env_file is not None and not Path(args.env_file).is_file():
        parser.exit(2, "Explicit environment file is missing\n")
    try:
        settings = Settings(_env_file=args.env_file)
    except ValidationError as error:
        fields = sorted({str(e["loc"][0]) for e in error.errors(include_input=False, include_context=False)})
        parser.exit(2, "Invalid configuration fields: " + ", ".join(fields) + "\n")
    uvicorn.run(create_app(settings), host=settings.app_host, port=settings.app_port, workers=1)


if __name__ == "__main__":
    main()
