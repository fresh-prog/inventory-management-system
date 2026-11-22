# pg_dump/pg_restore from the SAME major version as the database server (16)
FROM postgres:16-alpine
RUN apk add --no-cache bash openssl coreutils tar
COPY scripts/backup.sh scripts/verify_restore.sh /scripts/
COPY scripts/sql /scripts/sql
COPY deploy/backup/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
