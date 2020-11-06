
# ps_picker

Seismological P- and S- wave picker using the modified Kurtosis method

Python port of the picker described in Baillard et al., 2014

debugging information is saved to the local file run_{datetime}.log

## Methodology
The picker is based around the Kurtosis, but also uses energy levels, polarity,
clustering and phase association in a 3-step process:

### Step 1: define a global pick window

The *Kurtosis* is calculated for all stations.  The global window
surrounds the most densely clustered region of triggers.

### Step 2: pick P and S arrivals on each station individually

For each station:
    - calculate the *Kurtosis* over coarse to fine scales.
    - Identify candidates on the coarse scale and refine their times using
      the finier scales
    - Choose P- and S- candidates based on the *signal-to-noise level* of
      each pick
    - Verify the candidates using the waveform *polarity*, if possible
       - polarity is only used if one of the picks has a dip of > 30 degrees

### Step 3: associate picks
    - Calculate origin times for each trace, based on the P-S delay and
      a simple velocity model (could I use a single Vp/Vs value?)
    - If at least 3 origin times are clustered, use their average origin time
      to validate all candidates, possibly dipping into the pool of unused
      candidates for replacemene P and S picks
    - If less than 3 origin times are clustered, reject bad P- and S- picks
      based on clustering of P-pick times, S-pick times and P-S delays

## Code and parameter file examples
Are located [here](code_examples.md):


## Example workflow

### Start by autopicking a few events, with all the bells and whistles on:

To pick one event from a database in `/SEISAN/MAYOBS`:

    from ps_picker import PSPicker
    picker = PSPicker('parameters_C.yaml', '/SEISAN/MAYOBS/WAV/MAYOB',  '/SEISAN/MAYOBS/REA/MAYOB')
    picker.run_one('19-0607-59L.S201905', plot_global=True, plot_stations=True, verbose=True)


Look at all of the plots and verify that the picks and association are as
you expect.  If not, change the paramters and run again.

### Next, pick several events with only the global plots on

The bells and whistles text will be saved to a log file named
run_{DATETIME}.log

To pick events from May 5th to 25th in the same database:

    from ps_picker import PSPicker
    picker = PSPicker('parameters_C.yaml', '/SEISAN/MAYOBS/WAV/MAYOB',  '/SEISAN/MAYOBS/REA/MAYOB')
    picker.run_many('20190505', '20190525', plot_global=True)


### Finally, run the whole database without plots

*(run_{DATETIME}.log is always created)*

To pick events from May 26th 2019 May 1st 2020:

    from ps_picker import PSPicker
    picker = PSPicker('parameters_C.yaml', '/SEISAN/MAYOBS/WAV/MAYOB', '/SEISAN/MAYOBS/REA/MAYOB')
    picker.run_many('20190526', '20200501')


## Parameters
Picker parameters are passed in a
[YAML](https://tools.ietf.org/id/draft-pbryan-zyp-json-ref-03.html) file.  For
an example, go [here](code_examples.md)


## To Do

- Add event location-based acceptance of solitary P- and S- candidates
- In P-, S- and P-S clustering stage, allow unused candidates to be
  substituted for rejected picks
- More in [ToDo.md](ToDo.md)
    
Also see the [profiling file](profiling.md)
