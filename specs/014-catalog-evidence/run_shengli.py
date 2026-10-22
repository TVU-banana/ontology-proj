"""Explicitly authorized second Shengli trial, never an automatic content retry."""
import sys
import run_real

run_real.DATA=run_real.ROOT/'data/catalog-evidence-20261016-03'
run_real.RUNTIME=run_real.DATA/'runtime'
run_real.SAMPLES={'shengli':run_real.SAMPLES['shengli']}

if __name__=='__main__':
    {'prepare':run_real.prepare,'run':run_real.run}[sys.argv[1]]()
