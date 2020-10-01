# ps_picker

Seismological P- and S- wave picker using the modified Kurtosis method

Python port of the picker described in Baillard et al., 2014 

## Examples

To pick one event from a database in `/SEISAN/MAYOBS`:
::
  from ps_picker import PSPicker
  
  picker = PSPicker('/SEISAN/MAYOBS/WAV/MAYOB', 'parameters_C.yaml')
  picker.run_one('/SEISAN/MAYOBS/REA/MAYOB/2019/05/19-0607-59L.S201905')

To pick events from May to September 2019 in the same database:
::
  from ps_picker import PSPicker
  
  picker = PSPicker('/SEISAN/MAYOBS/WAV/MAYOB/', 'my_params.yaml')
  picker.run_many('/SEISAN/MAYOBS/REA/MAYOB/', '201905', '201909')

## Parameters
Picker parameters are passed in a
[YAML](https://tools.ietf.org/id/draft-pbryan-zyp-json-ref-03.html) file with
the following fields (fields with values shown have defaults and are not
required in the file):
```yaml
---
global_window: # Parameters affecting the initial selection of a global pick window
               # across all stations using the distribution of kurtosis extrema)
    frequency_band: [left, right] *cutoff frequencies for kurtosis calculation*
    - sliding_length:  *sliding window length in seconds for kurtosis calculation*
    - extrema_samples: *number of samples for the smoothing window when calculating extrema*
    - n_extrema: * number of extrema to keep for each trace*
    - distri_secs: seconds *size of window in which to look for the maximum # of picks*
    - offsets: [left, right] *final window offset [left, right] from peak distribution*
    - end_cutoff: 0.9  * don't look for extrema beyond this fraction of the overall time*
- SNR: *(Parameters affecting the signal-to-noise level calculation and use)*
    - noise_window_length: *seconds to use for noise window*
    - signal_window_length: *seconds to use for signal_window*
    - min_threshold_crossings: *Minimum crossings of SNR needed to accept a trace*
    - pick_quality_thresholds: [list of 4]: SNR levels associated with quality levels '3', '2', '1' and '0'
- dip_rect_thresholds: * minimum rectilinearity thresholds needed to assign 'P' or 'S' to an onset (P positive, S negative)*
    - P: 0.4
    - S: -0.4
- kurtosis: *(Parameters affecting the Kurtosis calculations (except in the inital global window selection)*
    - frequency_bands: *object with one or more "keys", each followed by a list of frequency bands over which to run Kurtosis e.g. {A: [[3, 15], [8, 30]]}*
    - window_lengths: *object with one or more "keys", each followed by a list of window lengths in seconds, e.g. {A: [0.3, 0.5, 1, 2, 4, 8]}*
    - smoothing_sequences: object with one or more "keys", each followed by a list of smooting sequences in samples, e.g. {A: [2, 4, 6, 8, 10, 20, 30, 40, 50]}*
- association: *(Parameters affecting the association between different stations)*
    - cluster_windows_P: *Window length in seconds for cluster-based rejection of P arrivals*
    - cluster_windows_S: *Window length in seconds for cluster-based rejection of S arrivals*
    - distri_min_values: *minimum number of values (P picks, S picks, or PS-times) needed for distribution-based rejection
    - distri_nstd_picks: 3.2 *reject picks outside of this number of standard deviations*
    - distri_nstd_delays: 4 *reject delays outside of this number of standard deviations*
- responsefiletype: ''
- responses:
    - filetype: ''
    - filename: (object with one or more "keys", each followed by a filename, e.g. {A: 'SPOBS2_response.txt', B: 'micrOBS_G1_response.txt'}
- station_parameters:  List of objects with key = station_name and values a dictionary with the following values:*
    - station1_name
        - P_comp: string of all components (one letter each) used for P-picks
        - S_comp: string of all components (one letter each) used for S-picks
        - f_nrg: frequency band [low, high] used for SNR and energy calculations
        - k_parms: *Kurtosis parameters*
            - freqs: *key from kurtosis:frequency_bands*
            - wind:  *key from kurtosis:window_lengths*
            - smooth: *key from kurtosis:smoothing_sequences*
        polar: *Use polarities (dip_rect thresholds) to assign P and S picks*
        nrg_win: *only look at data from t-nrg_win to t when evaluating
                 energy, where t is the time of the peak waveform energy.
                 If == 0, don't use energy criteria.*
        n_follow: *number of extrema to follow (1 or 2).  Generally use
                  2 (S and P) unless data are problematic*
        resp: *key from responses:filename*
    - station1_name
...
```

## More information

`TO DO`_

Use `reStructuredText
<http://docutils.sourceforge.net/rst.html>`_ to modify this file.


.. _TO DO: ToDo.rst

.. _JSONref: <https://tools.ietf.org/id/draft-pbryan-zyp-json-ref-03.html>
.. _YAML: <>