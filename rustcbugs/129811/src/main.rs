#![feature(async_closure)]

use std::future::Future;

async fn orchestrate_memd_routing<Fut: Future>(operation: impl Fn() -> Fut) {
    operation().await;
}

pub async fn orchestrate_simple_crud() {
    orchestrate_memd_routing(async || async {}.await).await;
}

fn main() {}