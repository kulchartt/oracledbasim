# dbasim — learn to fix Oracle from real incidents

dbasim plants a "the database is broken" incident in Oracle Database Free on your own computer.
A simulated app keeps using it the whole time. You find the cause, fix it, and dbasim checks
whether your fix really worked.

> ⚠️ dbasim creates and drops users and tablespaces in the database it points at. Only use it with
> Oracle Free on your own machine. It refuses to run against anything that is not Free and never
> drops a `SHOP` user it did not create.

## Requirements

- Docker (on Windows: Docker Desktop + WSL2)
- About 3GB free RAM and 10GB free disk
- Python 3.9 or newer

## 1. Start Oracle Database Free

```bash
docker run -d --name dbasim-oracle -p 1521:1521 \
  -e ORACLE_PWD=ChangeMe123 \
  container-registry.oracle.com/database/free:latest

docker logs -f dbasim-oracle   # wait for DATABASE IS READY TO USE!
```

Downloading and using Oracle Database Free is governed by the
[Oracle Free Use Terms and Conditions](https://www.oracle.com/downloads/licenses/oracle-free-license.html),
between you and Oracle.

## 2. Install dbasim

```bash
pip install dbasim
```

If `dbasim` is "not recognized" afterwards, your Python scripts folder is not on PATH. Either run every command as `python -m dbasim ...` instead of `dbasim ...`, or install with [pipx](https://pipx.pypa.io/) (`pipx install dbasim`), which puts it on PATH for you.

## 3. Configure (once)

```bash
dbasim setup
```

It asks for the SYSTEM password you gave the container (`ChangeMe123` above), saves it in `~/.dbasim/config.json`, and checks the connection. From then on every command works in any terminal window with nothing to export first.

Options: `dbasim setup --scale 0.3` makes the scenario data smaller for slow machines; `--dsn host:port/service` if Oracle is not on `localhost:1521/FREEPDB1`. The environment variables `DBASIM_ADMIN_PASSWORD`, `DBASIM_DSN` and `DBASIM_SCALE` still work and override the saved file (handy for scripts).

## 4. Play

| Command | What it does |
| --- | --- |
| `dbasim setup` | Save the database password once (`--scale`, `--dsn` optional) |
| `dbasim doctor` | Check Docker, the container, the database and the password |
| `dbasim list` | List scenarios and your scores |
| `dbasim start s01` | Start a scenario (plants the problem + starts the simulated app) |
| `dbasim logs` | Read the app's log, like when a ticket comes in |
| `dbasim ticket` | Show the current ticket again |
| `dbasim status` | Time spent, score, and the password of user SHOP |
| `dbasim hint` | Get a hint (-15 points each) |
| `dbasim check` | Check your fix; once solved you see a senior DBA's write-up |
| `dbasim solution --yes` | Give up and see the solution (0 points) |
| `dbasim reset` | Remove everything dbasim created and undo settings the scenarios invite you to change |

Use any tool you like: SQL*Plus, SQLcl, SQL Developer, connected as SYSTEM to `FREEPDB1`.
Every scenario can be solved with what Oracle Free ships (`v$` views, `DBMS_XPLAN`, trace).
No AWR/ASH needed.

## When you're done

```bash
dbasim stop                  # or: dbasim reset   (clears the scenario)
docker stop dbasim-oracle    # gives the memory back; docker start dbasim-oracle next time
```

Quit Docker Desktop before shutting the computer down. Shutting down while it is still running is
what causes its "An unexpected error occurred ... engine.sock" message on the next start.

## Troubleshooting

`dbasim doctor` checks the whole chain (Docker, the container, the database, the password) and
tells you which link is broken. The usual ones:

| Symptom | Fix |
| --- | --- |
| Docker Desktop (Windows/macOS) shows "An unexpected error occurred" mentioning `engine.sock` | Click **Quit**, open Docker Desktop again; it normally starts on the second try. Do **not** choose "Reset to factory defaults": that deletes the Oracle container and you would download it again. To avoid it, quit Docker Desktop before shutting the computer down. |
| "Docker engine is not running" | Open Docker Desktop and wait for "Engine running". |
| "container is stopped" (after a reboot) | `docker start dbasim-oracle`, wait about 30 seconds. |
| "database is still starting" | First start takes several minutes: `docker logs -f dbasim-oracle` until `DATABASE IS READY TO USE`. |
| `dbasim` is "not recognized" / "command not found" right after `pip install` | The Python scripts folder is not on PATH. Use `python -m dbasim ...`, or `pipx install dbasim`. |
| "No database password saved yet" | Run `dbasim setup` once. |
| "Oracle rejected the SYSTEM password" | `DBASIM_ADMIN_PASSWORD` must be the `ORACLE_PWD` you gave `docker run`. |
| Everything is slow or the container restarts | Give Docker at least 3GB of memory (Docker Desktop > Settings > Resources) or set `DBASIM_SCALE=0.3`. |

## Scenarios

| ID | Scenario | Level | Plan |
| --- | --- | --- | --- |
| s01 | Order search page is slow | Easy | Free |
| s02 | Month-end report slow after data migration | Easy | Free |
| s03 | The nightly batch fails every night | Easy | Free |
| s04 | Payment entry screen hangs | Medium | Pro (coming soon) |
| s05 | CPU spikes after a new app release | Medium | Pro (coming soon) |

The first three scenarios are free forever: no sign-up and no licence key. Pro scenarios are
not on sale yet (coming soon). Watch this repository to hear when they launch.

Found a problem, or want to suggest a scenario? Open an issue at
https://github.com/kulchartt/oracledbasim/issues
