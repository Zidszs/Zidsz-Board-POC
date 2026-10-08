# Teclado da janela, nao o stdin. powershell -File deixa Console.In no script.
# CONIN$ le o buffer da janela no PowerShell 5.1. Se o handle nao entrega
# tecla, o fallback e KeyAvailable/ReadKey. RawUI.KeyAvailable nao e porte:
# no 5.1 ele mente e o ReadKey seguinte trava. A tecla A (65) usa este leitor
# e, neste lote, e ignorada por quem chama.
$script:TecladoJanelaEstado = ""
$script:HandleTeclado = [IntPtr]::Zero
$script:TecladoModo = "fallback"

function Initialize-TecladoJanela {
    if ("N8Groker.TecladoJanela" -as [type]) {
        return
    }
    $codigo = @'
using System;
using System.Runtime.InteropServices;

namespace N8Groker {
    [StructLayout(LayoutKind.Explicit, Size = 20)]
    public struct RegistroEntrada {
        [FieldOffset(0)] public ushort EventType;
        [FieldOffset(4)] public int KeyDown;
        [FieldOffset(8)] public ushort RepeatCount;
        [FieldOffset(10)] public ushort VirtualKeyCode;
        [FieldOffset(12)] public ushort VirtualScanCode;
        [FieldOffset(14)] public ushort UnicodeChar;
        [FieldOffset(16)] public uint ControlKeyState;
    }

    public static class TecladoJanela {
        const uint GenericoLeitura = 0x80000000;
        const uint GenericoEscrita = 0x40000000;
        const uint CompartilhaLeitura = 1;
        const uint CompartilhaEscrita = 2;
        const uint AbreExistente = 3;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern IntPtr CreateFile(string nome, uint acesso, uint compartilha, IntPtr seguranca, uint disposicao, uint flags, IntPtr modelo);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetNumberOfConsoleInputEvents(IntPtr console, out uint quantidade);

        [DllImport("kernel32.dll", EntryPoint = "ReadConsoleInputW", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool ReadConsoleInput(IntPtr console, [Out] RegistroEntrada[] buffer, uint quantidade, out uint lidos);

        public static int Tamanho() {
            return Marshal.SizeOf(typeof(RegistroEntrada));
        }

        public static IntPtr Abrir() {
            IntPtr handle = CreateFile("CONIN$", GenericoLeitura | GenericoEscrita, CompartilhaLeitura | CompartilhaEscrita, IntPtr.Zero, AbreExistente, 0, IntPtr.Zero);
            if (handle == IntPtr.Zero || handle == new IntPtr(-1)) {
                return IntPtr.Zero;
            }
            return handle;
        }

        public static int Escolher(int[] tipos, int[] downs, int[] vks, int[] unis) {
            int n = tipos.Length;
            int ultima = 0;
            bool q = false;
            bool g = false;
            for (int i = 0; i < n; i++) {
                if (tipos[i] != 1 || downs[i] == 0) {
                    continue;
                }
                int vk = vks[i];
                int uni = unis[i];
                if (vk == 81 || uni == 81 || uni == 113) {
                    q = true;
                }
                if (vk == 71 || uni == 71 || uni == 103) {
                    g = true;
                }
                if (vk != 0) {
                    ultima = vk;
                } else if (uni == 113 || uni == 81) {
                    ultima = 81;
                } else if (uni == 103 || uni == 71) {
                    ultima = 71;
                } else if (uni != 0) {
                    ultima = uni;
                }
            }
            if (q) {
                return 81;
            }
            if (g) {
                return 71;
            }
            return ultima;
        }

        public static int Ler(IntPtr handle) {
            if (handle == IntPtr.Zero || Tamanho() != 20) {
                return 0;
            }
            uint quantidade;
            if (!GetNumberOfConsoleInputEvents(handle, out quantidade) || quantidade == 0) {
                return 0;
            }
            if (quantidade > 16) {
                quantidade = 16;
            }
            RegistroEntrada[] buffer = new RegistroEntrada[quantidade];
            uint lidos;
            if (!ReadConsoleInput(handle, buffer, quantidade, out lidos) || lidos == 0) {
                return 0;
            }
            int[] tipos = new int[lidos];
            int[] downs = new int[lidos];
            int[] vks = new int[lidos];
            int[] unis = new int[lidos];
            for (int i = 0; i < lidos; i++) {
                tipos[i] = buffer[i].EventType;
                downs[i] = buffer[i].KeyDown;
                vks[i] = buffer[i].VirtualKeyCode;
                unis[i] = buffer[i].UnicodeChar;
            }
            return Escolher(tipos, downs, vks, unis);
        }
    }
}
'@
    Add-Type -TypeDefinition $codigo -ErrorAction Stop
}

function Read-TeclaFallback {
    $script:TecladoModo = "fallback"
    try {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            $vk = [int]($key.Key)
            $uni = [int]$key.KeyChar
            if ($vk -eq 81 -or $uni -eq 81 -or $uni -eq 113) { return 81 }
            if ($vk -eq 71 -or $uni -eq 71 -or $uni -eq 103) { return 71 }
            if ($vk -ne 0) { return $vk }
            return $uni
        }
    } catch {
        return 0
    }
    return 0
}

function Read-TeclaJanela {
    $script:TecladoModo = "fallback"
    if ($script:TecladoJanelaEstado -eq "sem") {
        return (Read-TeclaFallback)
    }
    if ($script:TecladoJanelaEstado -ne "ok") {
        try {
            Initialize-TecladoJanela
            if ([N8Groker.TecladoJanela]::Tamanho() -ne 20) {
                $script:TecladoJanelaEstado = "sem"
                return (Read-TeclaFallback)
            }
            $script:HandleTeclado = [N8Groker.TecladoJanela]::Abrir()
            $script:TecladoJanelaEstado = "ok"
        } catch {
            $script:TecladoJanelaEstado = "sem"
            return (Read-TeclaFallback)
        }
    }
    if ($script:HandleTeclado -ne [IntPtr]::Zero) {
        try {
            $tecla = [int][N8Groker.TecladoJanela]::Ler($script:HandleTeclado)
            if ($tecla -ne 0) {
                $script:TecladoModo = "CONIN$"
                return $tecla
            }
        } catch {
            return (Read-TeclaFallback)
        }
    }
    return (Read-TeclaFallback)
}
