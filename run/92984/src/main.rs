struct Console<'gfx>(&'gfx usize);

impl<'gfx> Drop for Console<'gfx> {
    fn drop(&mut self) {}
}

fn main() {
    let mut gfx = 0;
    let mut console = Console(&gfx);

    for _ in 0..5 {
        println!("{gfx}");
        
        if gfx == 0 {
            drop(console);
            
            gfx += 1;
            
            console = Console(&gfx);
        }
    }
}