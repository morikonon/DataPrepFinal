#!/usr/bin/env bash
# Local Airflow (API server, scheduler, DAG processor, triggerer) with this folder as the DAGs folder.
set -euo pipefail
cd "$(dirname "$0")"

export AIRFLOW_HOME="$PWD/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$PWD"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Almaty
export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True
# macOS: without these, forked task processes crash on Objective-C initialisation and system proxy lookup.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export NO_PROXY="*"
export SE_AVOID_STATS=true

exec airflow "${@:-standalone}"
