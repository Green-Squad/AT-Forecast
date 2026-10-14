//! Direct official libSQL client. Every request pins one published generation.
use libsql::{Builder, Database, Transaction, TransactionBehavior, Value as SqlValue};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use std::{env, sync::Arc, time::Duration};

#[derive(Debug)]
pub struct Unavailable;

#[derive(Clone)]
pub struct Pool {
    database: Arc<Database>,
    timeout: Duration,
}

pub struct Session {
    transaction: Transaction,
    generation: i64,
}

impl Pool {
    pub fn from_database(database: Database, timeout: Duration) -> Self {
        Self {
            database: Arc::new(database),
            timeout,
        }
    }
    pub async fn from_env(timeout: Duration) -> Result<Self, String> {
        let url = env::var("TURSO_DATABASE_URL").map_err(|_| "Missing TURSO_DATABASE_URL")?;
        let isolated = env::var("TURSO_TEST_DATABASE").as_deref() == Ok("isolated")
            && (url.starts_with("http://127.0.0.1:")
                || url.starts_with("http://localhost:")
                || url.starts_with("http://at-fixture-libsql:"));
        if !url.starts_with("libsql://") && !url.starts_with("https://") && !isolated {
            return Err("TURSO_DATABASE_URL must use encrypted remote access".into());
        }
        let token = env::var("TURSO_AUTH_TOKEN").map_err(|_| "Missing TURSO_AUTH_TOKEN")?;
        let database = tokio::time::timeout(
            timeout,
            Builder::new_remote(url, token.trim().to_owned()).build(),
        )
        .await
        .map_err(|_| "Database initialization timed out")?
        .map_err(|_| "Database initialization failed")?;
        Ok(Self::from_database(database, timeout))
    }

    pub async fn read(&self) -> Result<Session, Unavailable> {
        tokio::time::timeout(self.timeout, async {
            let conn = self.database.connect().map_err(|_| Unavailable)?;
            let transaction = conn
                .transaction_with_behavior(TransactionBehavior::ReadOnly)
                .await
                .map_err(|_| Unavailable)?;
            let mut rows = transaction
                .query("SELECT generation FROM _migration_state WHERE id=1", ())
                .await
                .map_err(|_| Unavailable)?;
            let generation = rows
                .next()
                .await
                .map_err(|_| Unavailable)?
                .ok_or(Unavailable)?
                .get::<i64>(0)
                .map_err(|_| Unavailable)?;
            if generation <= 0 {
                return Err(Unavailable);
            }
            Ok(Session {
                transaction,
                generation,
            })
        })
        .await
        .map_err(|_| Unavailable)?
    }

    pub async fn close(&self) {}
}

impl Session {
    fn sql(&self, sql: &str) -> String {
        // All callers supply static application SQL. Values always use parameters.
        // These names are the inspected application tables, never user input.
        let mut out = sql.to_owned();
        for table in ["states", "shelters", "weathers", "hourly_weathers", "keys"] {
            for prefix in ["FROM ", "JOIN "] {
                for suffix in [" ", ")", "\n", ";"] {
                    out = out.replace(
                        &format!("{prefix}{table}{suffix}"),
                        &format!("{prefix}_g{}_{table} AS {table}{suffix}", self.generation),
                    );
                }
                if out.ends_with(&format!("{prefix}{table}")) {
                    out.truncate(out.len() - table.len());
                    out.push_str(&format!("_g{}_{table} AS {table}", self.generation));
                }
            }
        }
        out
    }

    pub async fn rows<T: DeserializeOwned>(
        &self,
        sql: &str,
        args: Vec<SqlValue>,
    ) -> Result<Vec<T>, Unavailable> {
        if !sql.trim_start().starts_with("SELECT ") {
            return Err(Unavailable);
        }
        let mut rows = self
            .transaction
            .query(&self.sql(sql), args)
            .await
            .map_err(|_| Unavailable)?;
        let mut out = Vec::new();
        while let Some(row) = rows.next().await.map_err(|_| Unavailable)? {
            let mut object = Map::new();
            let mut array = Vec::new();
            for i in 0..row.column_count() {
                let value = match row.get_value(i).map_err(|_| Unavailable)? {
                    SqlValue::Null => Value::Null,
                    SqlValue::Integer(n) => Value::from(n),
                    SqlValue::Real(n) => Value::from(n),
                    SqlValue::Text(s) => Value::String(s),
                    SqlValue::Blob(_) => return Err(Unavailable),
                };
                object.insert(
                    row.column_name(i).ok_or(Unavailable)?.to_owned(),
                    value.clone(),
                );
                array.push(value);
            }
            out.push(
                serde_json::from_value(Value::Object(object))
                    .or_else(|_| serde_json::from_value(Value::Array(array)))
                    .map_err(|_| Unavailable)?,
            );
        }
        Ok(out)
    }
}

pub fn text(v: impl ToString) -> SqlValue {
    SqlValue::Text(v.to_string())
}
pub fn integer(v: i32) -> SqlValue {
    SqlValue::Integer(v.into())
}

pub fn datetime<'de, D: serde::Deserializer<'de>>(d: D) -> Result<chrono::NaiveDateTime, D::Error> {
    use serde::Deserialize;
    let text = String::deserialize(d)?;
    chrono::NaiveDateTime::parse_from_str(&text, "%Y-%m-%d %H:%M:%S%.f")
        .map_err(serde::de::Error::custom)
}
