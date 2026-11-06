"""Executa Demucs com paralelismo adaptado aos núcleos disponíveis."""

from performance import cpu_threads


def main():
    import torch
    from demucs.separate import main as separate

    threads = cpu_threads()
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    separate()


if __name__ == "__main__":
    main()
