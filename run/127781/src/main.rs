#![feature(async_closure)]

use anyhow::Error;
use futures::{stream::repeat_with, StreamExt, TryStreamExt};

fn accept_str(_: &str) {}
fn accept_string(_: &String) {}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Error> {

    repeat_with(|| "foo".to_owned())
        .take(1)
        .map(Result::<_, Error>::Ok)
        .try_for_each(async move |value| {
            // error on whole closure, rust thinks that type of value is `str`
            accept_str(&value);

            // type annotations needed, cannot infer type
            accept_str(value.as_str());

            // this works
            accept_string(&value); // ok
            
            Ok(())
        })
        .await?;

    Ok(())
}