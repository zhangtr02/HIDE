trait MemoryUnit {
    extern "C" fn read_word(&mut self) -> u8;
    extern "C" fn read_dword(Self::Assoc<'_>) -> u16;
}

struct ROM {}

impl MemoryUnit for ROM {
    extern "C" fn read_dword(&'_ self) -> u16 {
        let a16 = self.read_word() as u16;
        let b16 = self.read_word() as u16;

        (b16 << 8) | a16
    }
}

pub fn main() {}