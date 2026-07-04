//! ikaros-memory-v3 CLI (Rust 实现, 1:1 替代 bin/ikaros-memory-v3.py)
//!
//! 用法:
//!   ikaros-memory-v3 store "哥哥喜欢 terse 中文" --type preference --weight 0.9
//!   ikaros-memory-v3 search "工作流" --top-k 5
//!   ikaros-memory-v3 stats
//!   ikaros-memory-v3 decay
//!   ikaros-memory-v3 access 1
//!   ikaros-memory-v3 clean
//!   ikaros-memory-v3 list --limit 20
//!   ikaros-memory-v3 get 1

use clap::{Parser, Subcommand};
use ikaros_memory_v3::{MemoryDB, Memory, Stats, WEIGHT_DEFAULT};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "ikaros-memory-v3")]
#[command(about = "伊卡洛斯记忆模块 v3 (DNA Memory 借鉴设计, Rust 实现)")]
struct Cli {
    #[arg(long, env = "IKAROS_MEMORY_DB",
           default_value = "E:/Ikaros/data/icarus-memory/v3.db")]
    db: PathBuf,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    Store {
        content: String,
        #[arg(long, default_value = "fact")]
        type_: String,
        #[arg(long, default_value_t = WEIGHT_DEFAULT)]
        weight: f32,
        #[arg(long, default_value = "")]
        tags: String,
    },
    Search {
        query: String,
        #[arg(long, default_value_t = 5)]
        top_k: i64,
    },
    Stats,
    List {
        #[arg(long, default_value_t = 20)]
        limit: i64,
        #[arg(long)]
        type_: Option<String>,
    },
    Get {
        memory_id: i64,
    },
    Access {
        memory_id: i64,
    },
    Delete {
        memory_id: i64,
    },
    Decay,
    Clean,
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let db = MemoryDB::new(&cli.db)?;
    match cli.cmd {
        Cmd::Store { content, type_, weight, tags } => {
            let id = db.store(&content, &type_, weight, &tags)?;
            println!("{{\"id\": {}, \"ok\": true}}", id);
        }
        Cmd::Search { query, top_k } => {
            let hits = db.search(&query, top_k)?;
            let json = serde_json::to_string_pretty(
                &serde_json::json!({
                    "results": hits,
                    "count": hits.len()
                }))?;
            println!("{}", json);
        }
        Cmd::Stats => {
            let s = db.stats()?;
            println!("{}", serde_json::to_string_pretty(&s)?);
        }
        Cmd::List { limit, type_ } => {
            let rows = db.list_all(limit, type_.as_deref())?;
            println!("{}", serde_json::to_string_pretty(
                &serde_json::json!({
                    "results": rows,
                    "count": rows.len()
                }))?);
        }
        Cmd::Get { memory_id } => {
            match db.get(memory_id)? {
                Some(m) => println!("{}", serde_json::to_string_pretty(&m)?),
                None => {
                    println!("{{\"error\": \"not found\"}}");
                    std::process::exit(2);
                }
            }
        }
        Cmd::Access { memory_id } => {
            db.access(memory_id)?;
            println!("{{\"ok\": true, \"memory_id\": {}}}", memory_id);
        }
        Cmd::Delete { memory_id } => {
            let ok = db.delete(memory_id)?;
            println!("{{\"ok\": {}, \"memory_id\": {}}}", ok, memory_id);
        }
        Cmd::Decay => {
            let n = db.decay()?;
            println!("{{\"decayed\": {}}}", n);
        }
        Cmd::Clean => {
            let n = db.clean()?;
            println!("{{\"cleaned\": {}}}", n);
        }
    }
    Ok(())
}
