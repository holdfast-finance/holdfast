# Setting up Anvil (Codex) and Flint (Grok): owner steps

ADR 0012 requires this before either agent starts. It gives them their own GitHub identity with
read-only access, a fork to push to, and an OS user that can't see your `.env`, SSH keys or `gh` login.
It takes about 15 minutes. When you're done, tell Keel the machine user's GitHub handle.

## 1. GitHub machine user
1. Create a new GitHub account for the agents (for example `holdfast-agents-bot`) with its own email
   (a routing address on `holdfastfinance.app` works). Turn on 2FA.
2. In `holdfast-finance`, add it to `holdfast` as an **outside collaborator with the Read role**.
   Read can't push, merge, add labels or change settings.
3. Org settings → Member privileges → allow forking of private repositories.
4. Repo settings → Actions → General → Fork pull request workflows: turn on **Run workflows from fork
   pull requests**. Leave **Send write tokens** and **Send secrets** off.
5. Signed in as the machine user, fork `holdfast-finance/holdfast`.

## 2. Separate OS user (Linux)
```sh
sudo useradd -m -s /bin/zsh holdfast-agents
sudo chmod 700 /home/holdfast-agents # this user's home, not only yours below
chmod 700 "$HOME"                    # your home: the agents can't read .env, ~/.ssh or ~/.config/gh
sudo -iu holdfast-agents
# as holdfast-agents:
#   install uv, Node 24 + pnpm, codex and grok for this user only
#   gh auth login                    # as the machine user, not as you
#   gh repo clone <bot>/holdfast ~/holdfast && cd ~/holdfast
#   git remote add upstream https://github.com/holdfast-finance/holdfast.git
#   make keys                        # throwaway dev secrets, no Plaid keys
#   codex login ; grok login         # your vendor accounts, stored in this user's home
```
Don't add `holdfast-agents` to the `docker` group (that's root-equivalent). DB and e2e tests run in
CI on the agents' PRs instead.

## 3. Check it
As `holdfast-agents`: `cat /home/<you>/Documents/CODE/holdfast/.env` must fail with permission denied,
and `gh api repos/holdfast-finance/holdfast --jq .permissions.push` must print `false`.

## 4. Start them
Use the commands at the bottom of `docs/planning/HANDOFF.md`.
