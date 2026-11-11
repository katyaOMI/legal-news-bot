"""Compatibility entry point: current privacy-risk preview, never Telegram."""
import argparse
from privacy_preview import run

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=6)
    parser.add_argument('--from-snapshot',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=10: parser.error('--limit must be 1..10')
    run(args.limit,args.from_snapshot)
