FROM postgres:15.19-alpine@sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81
COPY hand-forged/seed.sql /docker-entrypoint-initdb.d/10-hand-forged.sql
ENV POSTGRES_PASSWORD=HandForgedLocalDatabaseOnly POSTGRES_DB=fast_at_weather_production PGDATA=/var/lib/postgresql/data/ephemeral TZ=America/New_York
CMD ["postgres","-c","shared_buffers=16MB","-c","max_connections=12","-c","work_mem=1MB","-c","maintenance_work_mem=16MB","-c","max_wal_size=64MB","-c","min_wal_size=32MB","-c","timezone=America/New_York"]
