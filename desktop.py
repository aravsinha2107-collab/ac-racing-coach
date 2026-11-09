"""Launch the local recording review window: python desktop.py."""


def main():
    try:
        from ac_racing_coach.desktop import main as launch
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("PySide6"):
            print("Desktop dependencies are missing. Run: python -m pip install -r requirements-desktop.txt")
            return 1
        raise
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
