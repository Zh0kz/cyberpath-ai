# Security policy

CyberPath AI is a learning bot, not an active scanner. Please test only systems
you own or are authorized to use. The project currently provides best-effort
maintenance for the latest `main` branch; no response-time guarantee is made.

## Report a vulnerability privately

Use **Security → Advisories → Report a vulnerability** on the GitHub repository
if the button is available. This sends a private report to maintainers. If the
button is missing, do not put technical details in a public issue. Ask for a
private contact route in a short public issue without a proof of concept,
credentials, logs, or user data. Maintainers should enable GitHub's private
vulnerability reporting in repository settings.

In the private report, describe the affected version or commit, the impact,
safe reproduction steps, and a suggested fix if you have one. Use test tokens
and synthetic data. Do not include a live Telegram bot token, OpenRouter or
OpenAI key, private chat ID, SQLite database, or real user messages.

If a credential was exposed, revoke or rotate it immediately with its provider.
For a Telegram bot token, use BotFather to replace the token. A Git commit or
issue edit does not make an exposed credential secret again.

Please allow maintainers time to investigate and coordinate a fix before
publishing exploit details. Ordinary bugs and feature requests can use the
public issue templates.
