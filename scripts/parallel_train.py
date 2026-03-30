#!/usr/bin/env python3
"""Launch multiple GRU training processes to maximize GPU utilization."""
import atexit, os, signal, sys, json, subprocess, time
from pathlib import Path

ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'price_history'
MODEL_DIR   = ROOT / 'data' / 'models'
SCRATCH     = '/scratch/C00621463/pypackages'

NUM_PROCS   = 8       # separate OS processes — each gets its own GIL
WORKER_TIMEOUT = 2 * 60 * 60  # 2 hours max per worker
EPOCHS      = 60
MIN_DAYS    = 120


def get_remaining_symbols():
    """Find stocks with enough data that haven't been trained yet."""
    stock_counts = {}
    for f in sorted(HISTORY_DIR.glob('*.json')):
        try:
            day = json.loads(f.read_text())
            for sym in day.get('stocks', {}):
                stock_counts[sym] = stock_counts.get(sym, 0) + 1
        except Exception:
            continue

    already = set()
    for p in MODEL_DIR.glob('*_gru.pt'):
        already.add(p.stem.replace('_gru', ''))

    symbols = sorted(
        s for s, c in stock_counts.items()
        if c >= MIN_DAYS and s.replace('/', '_') not in already
    )
    return symbols


def chunk_list(lst, n):
    """Split list into n roughly equal chunks."""
    k, m = divmod(len(lst), n)
    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]


def _kill_all(procs):
    """Terminate then kill all child processes."""
    for _, p, lf, _ in procs:
        try:
            p.terminate()
        except OSError:
            pass
    deadline = time.time() + 5
    for _, p, lf, _ in procs:
        remaining = max(0, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass
    for _, _, lf, _ in procs:
        try:
            lf.close()
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Parallel GRU training')
    parser.add_argument('--full', action='store_true',
                        help='Full GA/PSO search (slower, ~1hr). Default is fast mode (~3min)')
    parser.add_argument('--retrain-all', action='store_true',
                        help='Retrain all stocks (delete existing models first)')
    pargs = parser.parse_args()

    fast_mode = not pargs.full

    if pargs.retrain_all:
        for p in MODEL_DIR.glob('*_gru.pt'):
            p.unlink()
        print('Cleared all existing GRU models.')

    symbols = get_remaining_symbols()
    print('Remaining stocks to train: %d' % len(symbols))
    print('Mode: %s' % ('FAST (defaults + FP16)' if fast_mode else 'FULL (GA/PSO search)'))

    if not symbols:
        print('All stocks already trained.')
        return

    n_procs = min(NUM_PROCS, len(symbols))
    chunks = chunk_list(symbols, n_procs)

    env = os.environ.copy()
    env['PYTHONPATH'] = SCRATCH + ':' + env.get('PYTHONPATH', '')

    procs = []
    start_times = {}  # worker index -> start timestamp

    for i, chunk in enumerate(chunks):
        sym_list = ','.join(chunk)
        log_path = ROOT / 'logs' / ('gru_worker_%d.log' % i)

        log_file = None
        try:
            log_file = open(str(log_path), 'w')

            cmd = [
                sys.executable, str(ROOT / 'ml' / 'gru_predictor.py'),
                '--train', '--skip-trained',
                '--epochs', str(EPOCHS),
            ]
            if fast_mode:
                cmd.append('--fast')
            # Pass symbols via environment variable
            proc_env = env.copy()
            proc_env['TRAIN_SYMBOLS'] = sym_list

            p = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                                 env=proc_env, cwd=str(ROOT))
        except Exception as e:
            if log_file is not None:
                log_file.close()
            print('  Worker %d: FAILED to launch: %s' % (i, e))
            continue

        procs.append((i, p, log_file, chunk))
        start_times[i] = time.time()
        print('  Worker %d: PID=%d, %d stocks (%s ... %s)' % (
            i, p.pid, len(chunk), chunk[0], chunk[-1]))

    if not procs:
        print('No workers launched successfully.')
        return

    # Register signal handlers and atexit to clean up children on parent death
    def _cleanup_handler(signum=None, frame=None):
        _kill_all(procs)
        if signum is not None:
            sys.exit(128 + signum)

    signal.signal(signal.SIGINT, _cleanup_handler)
    signal.signal(signal.SIGTERM, _cleanup_handler)
    atexit.register(lambda: _kill_all(procs))

    print('\n%d workers launched. Monitoring...\n' % len(procs))

    while True:
        time.sleep(30)
        now = time.time()
        alive = 0
        for idx, p, lf, _ in procs:
            if p.poll() is None:
                # Check for timeout
                elapsed = now - start_times.get(idx, now)
                if elapsed > WORKER_TIMEOUT:
                    print('[%s] Worker %d (PID=%d) exceeded %ds timeout, killing.' % (
                        time.strftime('%H:%M:%S'), idx, p.pid, WORKER_TIMEOUT))
                    try:
                        p.kill()
                    except OSError:
                        pass
                else:
                    alive += 1

        done_models = len(list(MODEL_DIR.glob('*_gru.pt')))
        print('[%s] %d/%d workers alive, %d models trained' % (
            time.strftime('%H:%M:%S'), alive, len(procs), done_models))
        if alive == 0:
            break

    for _, _, lf, _ in procs:
        try:
            lf.close()
        except Exception:
            pass

    done_models = len(list(MODEL_DIR.glob('*_gru.pt')))
    print('\nAll workers finished. Total models: %d' % done_models)


if __name__ == '__main__':
    main()
