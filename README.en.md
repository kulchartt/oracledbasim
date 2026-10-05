# dbasim — learn to fix Oracle from real incidents

Thai is the default language; run `dbasim lang en` for English.

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

## 3. Configure

```bash
export DBASIM_ADMIN_PASSWORD=ChangeMe123          # Windows PowerShell: $env:DBASIM_ADMIN_PASSWORD="ChangeMe123"
export DBASIM_DSN=localhost:1521/FREEPDB1           # default
export DBASIM_SCALE=0.3                             # smaller data for slow machines
dbasim lang en                                      # English (Thai is the default)
dbasim doctor
```

## 4. Play

| Command | What it does |
| --- | --- |
| `dbasim list` | List scenarios and your scores |
| `dbasim start s01` | Start a scenario (plants the problem + starts the simulated app) |
| `dbasim logs` | Read the app's log, like when a ticket comes in |
| `dbasim status` | Time spent, score, and the password of user SHOP |
| `dbasim hint` | Get a hint (-15 points each) |
| `dbasim check` | Check your fix; once solved you see a senior DBA's write-up |
| `dbasim solution --yes` | Give up and see the solution (0 points) |
| `dbasim lang en` / `th` | Switch language (or set `DBASIM_LANG`) |
| `dbasim reset` | Remove everything dbasim created and undo settings the scenarios invite you to change |

Use any tool you like: SQL*Plus, SQLcl, SQL Developer, connected as SYSTEM to `FREEPDB1`.
Every scenario can be solved with what Oracle Free ships (`v$` views, `DBMS_XPLAN`, trace).
No AWR/ASH needed.

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
