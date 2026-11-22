import os
import random

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "fixtures", "longctx", "corpus.txt")
rnd = random.Random(42)
jails = ["postfix-sasl", "sshd", "recidive", "grommunio-web"]
actions = ["BAN", "UNBAN", "NOK", "INFO"]
zones = ["FR", "DE", "US", "RU", "BR", "KR"]

lines = []
for i in range(3000):
    ip = f"198.51.{rnd.randrange(100, 200)}.{rnd.randrange(2, 254)}"
    hh = rnd.randrange(0, 24)
    mm = rnd.randrange(0, 60)
    ss = rnd.randrange(0, 60)
    jail = rnd.choice(jails)
    action = rnd.choice(actions)
    zone = rnd.choice(zones)
    lines.append(f"2026-11-{rnd.choice(['18','19','20'])}T{hh:02d}:{mm:02d}:{ss:02d}Z "
                 f"filterlog fail2ban[{action}] ip={ip} jail={jail} geo={zone} "
                 f"rule={rnd.randrange(1, 9)} src_port={rnd.randrange(1024, 65535)} "
                 f"session={i:08d}-{'x' * 42}")

lines[1873] = ("2026-11-19T04:02:17Z fail2ban.actions: BAN 198.51.100.73 "
               "jail=postfix-sasl geo=FR reason=6_failed_AUTH_on_port_587 "
               "session=00001873-FR-postfix-sasl-confirmed")

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
print(OUT, os.path.getsize(OUT))
