# pg_dump/pg_restore from the SAME major version as the database server (16).
# The Debian-based image already contains bash, openssl, tar and coreutils, so the build downloads no
# extra packages (works behind restrictive firewalls).
FROM postgres:16
COPY scripts/backup.sh scripts/verify_restore.sh /scripts/
COPY scripts/sql /scripts/sql
COPY deploy/backup/entrypoint.sh /entrypoint.sh
# Robust to checkouts made on Windows: strip CR line endings and set the executable bit.
RUN sed -i 's/\r$//' /entrypoint.sh /scripts/*.sh && chmod 755 /entrypoint.sh /scripts/*.sh
ENTRYPOINT ["/entrypoint.sh"]
