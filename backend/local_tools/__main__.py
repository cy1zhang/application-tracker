import sys

from local_tools import auth
from local_tools.config import load_config


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("login", "logout"):
        print("usage: python -m local_tools login|logout", file=sys.stderr)
        return 2
    config = load_config("../.env.local-tools")
    if argv[1] == "login":
        auth.login(config)
        print("Signed in.")
    else:
        auth.logout(config)
        print("Signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
