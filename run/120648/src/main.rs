//lib.rs in another crate that depends on the jewel crate
#![no_std]
#![allow(async_fn_in_trait)]

use jewel::BleRadio;

pub struct Radio {}

impl BleRadio for Radio {
    fn set_buffer(&mut self, buffer: &[u8]) {}
    async fn transmit(&mut self) {}
}

fn main() {}