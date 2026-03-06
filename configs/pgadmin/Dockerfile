FROM dpage/pgadmin4:latest
USER root
RUN printf 'coderz-db:5432:*:coderapi:coderapi_2024\npostgres:5432:*:prefect:prefect_coderz_2024\n' \
    > /pgadmin4/.pgpass && chmod 600 /pgadmin4/.pgpass && chown pgadmin:root /pgadmin4/.pgpass
USER pgadmin
