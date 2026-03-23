"""
Automated setup for Polymarket AI Trading Bot.
Run: python setup.py
"""
import subprocess, sys, os, shutil

def run(cmd):
    return subprocess.run(cmd, shell=True).returncode == 0

def main():
    print("\n[START] Polymarket AI Trading Bot — Setup\n" + "="*45)

    v = sys.version_info
    if v < (3, 9):
        print(f"[FAIL] Python 3.9+ required (found {v.major}.{v.minor})")
        sys.exit(1)
    print(f"[OK] Python {v.major}.{v.minor}.{v.micro}")

    if not os.path.exists(".venv"):
        print("[SETTINGS]  Creating virtual environment…")
        if not run(f"{sys.executable} -m venv .venv"):
            sys.exit(1)
    print("[OK] Virtual environment ready")

    pip = ".venv/bin/pip" if os.name != "nt" else r".venv\Scripts\pip"
    python = ".venv/bin/python" if os.name != "nt" else r".venv\Scripts\python"

    print("[PKG] Installing dependencies…")
    run(f"{pip} install --upgrade pip -q")
    if not run(f"{pip} install -r requirements.txt -q"):
        print("[FAIL] Some dependencies failed — check requirements.txt")
    print("[OK] Dependencies installed")

    if not os.path.exists(".env"):
        shutil.copy("env.template", ".env")
        print("[OK] .env created from template")
    else:
        print("[INFO]  .env already exists")

    print("[DB]  Initialising database…")
    run(f'{python} -c "import asyncio; from src.utils.database import DatabaseManager; asyncio.run(DatabaseManager().initialize())"')
    print("[OK] Database ready")

    print("\n" + "="*45)
    print("[OK] Setup complete!\n")
    print("Quick start:")
    print("  1. Fill in .env:")
    print("     GROQ_API_KEY    → console.groq.com  (free)")
    print("     GEMINI_API_KEY  → aistudio.google.com  (free)")
    print()
    print("  2. Reset demo state:")
    print("     python scripts/reset_demo.py")
    print()
    print("  3. Run paper trading:")
    print("     python cli.py run --paper")
    print()
    print("  4. Open dashboard (new terminal):")
    print("     streamlit run dashboard.py")
    print()
    print("  5. Run backtest (no keys needed):")
    print("     python cli.py backtest --offline --report")

if __name__ == "__main__":
    main()
