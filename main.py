"""Sheila's text-first terminal development interface."""

import memory
from sheila_handler import process_message


def main() -> None:
    memory.init_db()
    print("Sheila is online. Type a message and press Enter. Type /quit to exit.")
    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down.")
            return
        if user_text.lower() == "/quit":
            print("Shutting down.")
            return
        if not user_text:
            continue
        try:
            print(f"Sheila: {process_message(user_text)}")
        except Exception as exc:
            print(f"Sheila: I ran into an error: {exc}")


if __name__ == "__main__":
    main()
