# ARC Chat macOS wrapper

Double-click `ARC Chat.app`. It opens the chat in your default browser and runs the helper in the background. Use **Quit helper** in the chat to stop it. Stop the OOD allocation separately when finished.

If the old Terminal helper is still running, stop it with Control-C first. The app never terminates an existing helper or other process occupying port 8765.

This is a local, ad-hoc-signed app, not an Apple-notarized distribution. It requires Python 3.10+. On this Mac it reuses the existing arc-chat virtual environment and Chromium installation. If that folder is moved or removed, the app installs dependencies into `~/Library/Application Support/ARC Chat` on first launch. Internet access is needed for that setup. The HTML and helper source are included inside the app.

Startup diagnostics are written to `~/Library/Application Support/ARC Chat/launcher.log`. The private session file allows reopening the chat when the app is double-clicked again. No login credentials are stored there.

The app can be moved to Applications. It is not a fully self-contained Python runtime distribution for other Macs.

The launcher copies helper.py and arc-chat.html into its stable Application Support/app directory before starting. Moving the app bundle while running therefore does not remove the files being served. Reopening checks the existing helper first and preserves its kernel.
