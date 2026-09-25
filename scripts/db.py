"""The database the web app uses: set it up, make accounts, bring your data in.

    docker compose up -d db                          # the local database (compose.yaml)
    uv run python scripts/db.py migrate              # create or update its tables
    uv run python scripts/db.py status               # which migrations it has
    uv run python scripts/db.py create-user you@example.com --name "You" --locale nl
    uv run python scripts/db.py invite tester@example.com       # account + login link
    uv run python scripts/db.py login-link tester@example.com   # a new link
    uv run python scripts/db.py set-role you@example.com owner
    uv run python scripts/db.py new-secret           # for JOBLENS_SECRET_KEY (own keys)
    uv run python scripts/db.py import you@example.com          # runs and labels
    uv run python scripts/db.py import you@example.com --cv data/raw/cv/you.pdf \\
        --strip-name "Your Name"                                 # and your CV
    uv run python scripts/db.py purge-files          # old files, links, sessions
    uv run python scripts/db.py delete-user you@example.com --yes

Which database: DATABASE_URL in .env (the local one in .env.example; Neon's
connection string when hosted). The scripts, the evals and the viewer keep
using the files in data/raw/; `serve.py --db EMAIL` shows an account's runs.

**Signing in is invite-only (7.5).** `invite` makes an account (a tester,
unless --owner) and prints a login link; send it to them however you like. The
link works once, within 7 days, and gives a 30-day session; `login-link` makes a
new one. The links point at JOBLENS_BASE_URL (default http://127.0.0.1:8001).

`import` copies this laptop's runs (data/raw/cv-runs/) and a real CV's labels
(data/raw/cv-labels/) into one account, and can be run again: what is already
there is skipped. The four invented CVs' labels in evals/cv-matches/ are the
repo's evidence, not anybody's data, and are never imported.
"""

import argparse
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.cv.match import prepare_cv
from joblens.cv.runs import digest
from joblens.cv.store import CVCache
from joblens.llm.client import LLMClient
from joblens.llm.structured import default_mode
from joblens.storage import Database, FileStore
from joblens.storage.migrate import MigrationError, status

ROOT = Path(__file__).parent.parent
LOCALES = ["en", "nl", "de", "fr", "es"]
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
CACHE_DIR = ROOT / "data" / "cache"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or update the tables")
    commands.add_parser("status", help="which migrations this database has")
    make = commands.add_parser("create-user", help="make an account")
    make.add_argument("email")
    make.add_argument("--name")
    make.add_argument("--locale", choices=LOCALES)
    make.add_argument(
        "--owner", action="store_true", help="unlimited; the rest are testers"
    )
    invite = commands.add_parser("invite", help="an account and a login link")
    invite.add_argument("email")
    invite.add_argument("--name")
    invite.add_argument("--locale", choices=LOCALES)
    invite.add_argument("--owner", action="store_true")
    link = commands.add_parser("login-link", help="a new login link")
    link.add_argument("email")
    commands.add_parser("new-secret", help="a key to encrypt own API keys with")
    role = commands.add_parser("set-role", help="make someone owner or tester")
    role.add_argument("email")
    role.add_argument("role", choices=["owner", "tester"])
    bring = commands.add_parser("import", help="copy this laptop's data in")
    bring.add_argument("email")
    bring.add_argument("--cv", type=Path, help="also store this CV as the active one")
    bring.add_argument("--strip-name", metavar="NAME")
    commands.add_parser(
        "purge-files", help="delete uploaded files past 30 days, old links and sessions"
    )
    gone = commands.add_parser("delete-user", help="an account and all its data")
    gone.add_argument("email")
    gone.add_argument("--yes", action="store_true", help="really delete it")
    args = parser.parse_args()

    load_dotenv()
    if args.command == "new-secret":
        return new_secret(None, args)  # needs no database
    try:
        database = Database.from_env()
        return COMMANDS[args.command](database, args)
    except (ValueError, MigrationError) as err:
        print(err)
        return 1


def migrate(database: Database, args) -> int:
    applied = database.migrate()
    for one in applied:
        print(f"applied {one.name}")
    print("up to date." if not applied else f"{len(applied)} applied.")
    return 0


def show_status(database: Database, args) -> int:
    with database.connect() as conn:
        for one in status(conn):
            when = f"{one.applied_at:%Y-%m-%d %H:%M}" if one.applied_at else "NOT YET"
            print(f"  {when:<16}  {one.migration.name}")
    return 0


def create_user(database: Database, args) -> int:
    user = database.create_user(
        email=args.email,
        display_name=args.name,
        locale=args.locale,
        role="owner" if args.owner else "tester",
    )
    print(f"created {user.email} as {user.role}  ({user.id})")
    return 0


def invite(database: Database, args) -> int:
    """An account if there is none, and a login link either way."""
    user = database.user_by_email(args.email)
    if user is None:
        create_user(database, args)
        user = database.user_by_email(args.email)
    return print_link(database, user)


def login_link(database: Database, args) -> int:
    user = database.user_by_email(args.email)
    if user is None:
        print(f"No account for {args.email}: invite them first.")
        return 1
    return print_link(database, user)


def new_secret(database: Database | None, args) -> int:
    """A fresh JOBLENS_SECRET_KEY. Keep it out of git, and keep it: every stored
    own key is encrypted with it, and a new one means entering them again."""
    print(f"JOBLENS_SECRET_KEY={Fernet.generate_key().decode()}")
    return 0


def set_role(database: Database, args) -> int:
    user = database.user_by_email(args.email)
    if user is None:
        print(f"No account for {args.email}.")
        return 1
    print(f"{database.set_role(user.id, args.role).email} is now {args.role}")
    return 0


def print_link(database: Database, user) -> int:
    base = os.environ.get("JOBLENS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    token = database.create_login_link(user.id)
    print(
        f"login link for {user.email} (works once, for 7 days):\n"
        f"  {base}/login#{token}\n"
        "Send it to them yourself; JobLens keeps only a hash of it."
    )
    return 0


def import_data(database: Database, args) -> int:
    user = database.user_by_email(args.email)
    if user is None:
        print(f"No account for {args.email}: create-user first.")
        return 1
    files, store = FileStore(ROOT), database.store_for(user.id)

    have = {one.id for one in store.runs()}
    # Oldest first, and by id within a minute, so that two runs of one minute
    # get the same "-2" in the database as they have on disk.
    stored = sorted(files.runs(), key=lambda one: (one.at, one.id))
    copied = 0
    for summary in stored:
        if summary.id in have:
            continue
        new_id = store.save_run(files.load_run(summary.id))
        copied += 1
        if new_id != summary.id:
            print(f"  note: {summary.id} is {new_id} in the database")
    print(f"runs: {copied} copied, {len(stored) - copied} already there")

    private = [one for one in files.labels() if not files.is_shared_labels(one.cv)]
    for labels in private:
        store.save_labels(labels)
    print(f"labels: {len(private)} CV(s) -- {', '.join(one.cv for one in private)}")

    if args.cv:
        return import_cv(store, args)
    return 0


def import_cv(store, args) -> int:
    """Read, redact and profile the CV exactly as match_cv.py does. The profile
    comes from the cache when this CV was matched before, so this is free."""
    settings = load_llm_settings(prefix="CV")
    with LLMClient(settings) as client:
        prepared = prepare_cv(
            args.cv,
            client,
            name=args.strip_name,
            model=settings.model,
            mode=default_mode(settings),
            cache=CVCache(CACHE_DIR / "cv-profiles.json"),
        )
    active = store.active_cv()
    if active and active.digest == digest(prepared.text):
        print(f"cv: {active.filename} is already the active CV")
        return 0
    kept = store.add_cv(
        args.cv.name,
        prepared.text,
        profile=prepared.profile,
        strip_name=args.strip_name,
        original=args.cv.read_bytes(),
    )
    source = "from the cache" if prepared.from_cache else "read by the model"
    print(f"cv: {kept.filename} stored as the active CV (profile {source})")
    return 0


def purge_files(database: Database, args) -> int:
    print(f"{database.purge_expired_files()} expired file(s) deleted")
    print(f"{database.purge_expired_logins()} expired link(s) and session(s) deleted")
    return 0


def delete_user(database: Database, args) -> int:
    user = database.user_by_email(args.email)
    if user is None:
        print(f"No account for {args.email}.")
        return 1
    if not args.yes:
        print(
            f"This deletes {args.email} and every CV, file, run, label and "
            "preference of theirs. Run again with --yes to do it."
        )
        return 1
    database.delete_user(user.id)
    print(f"deleted {args.email} and everything they stored")
    return 0


COMMANDS = {
    "migrate": migrate,
    "status": show_status,
    "create-user": create_user,
    "invite": invite,
    "login-link": login_link,
    "set-role": set_role,
    "new-secret": new_secret,
    "import": import_data,
    "purge-files": purge_files,
    "delete-user": delete_user,
}


if __name__ == "__main__":
    sys.exit(main())
