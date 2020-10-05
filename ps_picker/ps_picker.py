# Generated with SMOP  0.41-beta
# from smop.libsmop import *

# Standard libraries
import os.path
import warnings
import glob
import logging
import sys

# Publicly available libraries
import numpy as np
from scipy import stats
from obspy.core import UTCDateTime
from obspy.core.event import Catalog as obspy_Catalog
# from obspy.core.inventory.response import PolesZerosResponseStage
from obspy.core.event.magnitude import Amplitude as obspy_Amplitude
from obspy.core.event.origin import Pick as obspy_Pick
from obspy.core.event.origin import Origin as obspy_Origin
from obspy.core.event import Event as obspy_Event
from obspy.core.event.base import WaveformStreamID, QuantityError
from obspy.core.stream import Stream
# from obspy.signal.invsim import simulate_seismometer
from obspy.io.nordic.core import read_nordic
from obspy.core import read as obspy_read

# module libraries
from .parameters import (PickerParameters, PickerRunParameters,
                         PickerLoopParameters)
from .kurtosis import Kurtosis
from .plotter import Plotter
from .trace_utils import (fast_polar_analysis, mean_trace, pk2pk,
                          select_traces, smooth_filter, snr_function)
from .utils import (clean_distri, cluster_clean, get_response,
                    picks_ps_times, picks_matched_stations)


class PSPicker():
    """
    Pick P and S arrivals on multiple stations using the Kurtosis and
    do a very basic cluster-based association
    """
    def __init__(self, wav_base_path, parm_file,
                 rea_path_out='./Sfile_directory', verbose=True,
                 debug=False):
        """
        :param wav_base_path: absolute basepath to the waveform files
            (just before the YEAR/MONTH subdirectories)
        :param parm_file: path/name of the parameter file
        :param rea_path_out: path to output REA files
        :param verbose: talk a bit
        """
        self.wav_base_path = wav_base_path
        self.parm_file = parm_file
        self.rea_path_out = rea_path_out
        self.rea_name = None
        self.param = PickerParameters.from_yaml_file(parm_file)
        self.verbose = verbose
        self.debug = debug
        self.run = None

#    @property
#    def stations(self):
#        return self.param.station_parameters.stations
#
#    @property
#    def n_stations(self):
#        return self.param.station_parameters.n_stations

    def __str__(self):
        """
        """
        str = "PSPicker\n"
        str += f"    wav_base: {self.wav_base_path}\n"
        str += f"    output db directory: {self.rea_path_out}\n"
        str += f"    parameters: {self.param}\n"
        return str

    def run_many(self, rea_base_path_in, start_yearmonth, end_yearmonth,
                 plot_global=True, plot_stations=False):
        """
        Loops over events in a date range

        :param rea_base_path_in: absolute basepath to the input datbase
            files (just before the YEAR/MONTH subdirectories)
        :param start_yearmonth: "YYYYMM" of first month to process
        :param end_yearmonth: "YYYYMM" of last month to process
        :param plot_global: show global and overall pick plots
        :param plot_stations: show individual station plots
        """
        self.rea_base_path_in = rea_base_path_in
        start_year = float(start_yearmonth[:4])
        start_month = float(start_yearmonth[4:6])
        end_year = float(end_yearmonth[:4])
        end_month = float(end_yearmonth[4:6])
        debug_fname =\
            'ps_picker_debug_{:04d}.{:02d}-{:04d}.{:02d}_run{}.txt'.format(
                start_year, start_month, end_year, end_month,
                UTCDateTime.now().strftime('%Y.%m.%d'))
        for year in range(start_year, end_year + 1):
            if year == start_year:
                first_month = start_month
            else:
                first_month = 1
            if year == end_year:
                last_month = end_month
            else:
                last_month = 12
            for month in range(first_month, last_month + 1):
                self.log(f'{year:04d}/{month:02d}')
                s_files = glob.glob(os.path.join(
                    rea_base_path_in, f'{year:04d}', f'{month:02d}', '*.S*'))
                for s_file in s_files:
                    try:
                        self.run_one(s_file, plot_global=plot_global,
                                     plot_stations=plot_stations)
                    except Exception as err:
                        warnings.warn('Pick_Function failed for {s_file}')
                        self._write_debug_file(debug_fname, err, s_file)

    def _write_debug_file(self, debug_fname, err, s_file):
        """
        Write debugging information after a failed run_one

        :param debug_fname: debug file name
        :param err: returned Exception error
        :param s_file: name of the s_file corresponding to this event
        """
        with open(debug_fname, 'a') as fid:
            fid.write('-'*60 + '\n')
            fid.write(f'{err}\n')
            fid.write('To reproduce the error, type:\n')
            fid.write(f'    picker.run_one("{s_file}"\n\n')

    def run_one(self, rea_name, plot_global=True, plot_stations=False,
                verbose=None, debug=False):
        """
        Picks P and S arrivals on one waveform, using the Kurtosis

        Information in the database file will be appended with the picks.
        :param rea_name: database file to read (full path)
        :param plot_global: show global and overall pick plots
        :param plot_stations: show individual station plots
        """
        # Run basic Kurtosis and assoc to find most likely window for picks
        self._setup(rea_name, plot_global, plot_stations)

        self.debug = debug
        if verbose is not None:
            self.verbose = verbose

        amplitudes, picks, iter = [], [], 0
        # Pick on individual traces
        for station_name, chan_map in sorted(self.run.channel_maps.items(),
                                             key=lambda x: x[0]):
            # Reject stations not listed in parameter file
            if station_name not in self.param.stations:
                continue
            sta_parans = self.param.station_parameters[station_name]
            self.loop = PickerLoopParameters(station=station_name,
                                             station_params=sta_parans,
                                             channel_map=chan_map)
            self.loop.add_component_traces(self.run.stream)
            self.run.plotter.station_window_setup(self.loop.datP[0], iter)
            i_onset_P, i_onset_S = None, None  # samps after self.loop.t_begin

            # SNR analysis
            fe = self.loop.station_params.f_energy
            datS_filtered = self.loop.datS.copy().filter(
                'bandpass', corners=3, freqmin=fe[0], freqmax=fe[1])
            snr, energy = self._calc_snr(datS_filtered)
            if self._snr_trustworthy(snr):
                if self.verbose:
                    self.log(f"{station_name}: snr is trustworthy", level='verbose')
                extrema, i_onset_P, i_onset_S = self._run_Kurtosis(snr, energy)
                self.log(f'i_onset_P={i_onset_P}, i_onset_S={i_onset_S}',
                         level='debug')

                # Verify phases using Polarity analysis
                if self.loop.station_params.use_polarity\
                        and (len(datS_filtered) == 3):
                    i_onset_P, i_onset_S = self._polarity_analysis(
                        extrema, datS_filtered)
                    self.log('polarity-verified i_onset_P={}, i_onset_S={}'
                          .format(i_onset_P, i_onset_S))
            elif self.verbose:
                self.log(f"{station_name}: snr is not trustworthy, not picking", level='verbose')

            self.run.plotter.station_window_Ptrace(self, iter)
            self.run.plotter.plot_onsets(self, iter, i_onset_P, i_onset_S)

            new_picks = self._make_picks(i_onset_P, i_onset_S, snr)
            picks.extend(new_picks)
            amp = self._calc_amplitude(new_picks, snr)
            if amp is not None:
                amplitudes.append(amp)
            iter += 1

        # Jacknife ###########
        picks = self._remove_unassociated(picks)

        self.run.plotter.pick_window_add_picks(
            picks, self.run.stations, self.loop.t_begin,
            self.param.assoc_cluster_window_P,
            self.param.assoc_cluster_window_S)
        self._save_event(picks, amplitudes)

    def _setup(self, rea_name, plot_global, plot_stations):
        """
        Setup starting parameters and objects

        :rea_name: database file to read (full path)
        :param plot_global: show global and overall pick plots
        :param plot_stations: show individual station plots
        """
        plotter = Plotter(plot_global, plot_stations)
        full_wavefile = self._setuprun_get_wavefile_name(rea_name)
        stream = obspy_read(full_wavefile, 'MSEED')
        t_begin = min([t.stats.starttime for t in stream])
        t_end = max([t.stats.endtime for t in stream])
        # get rid of bad last sample in some streams, and detrend
        for tr in stream:
            tr.data = tr.data[:-10]
            tr.detrend(type='demean')
        # self.log(self.param.channel_mapping_rules, level='debug')
        channel_maps = select_traces(stream, self.param.channel_mapping_rules)
        # self.log(channel_maps, level='debug')
        overall_distri, channel_maps, plotter =\
            self._setuprun_remove_problem_stations(stream, channel_maps,
                                                   plotter, t_begin)
        ft, lt, overall_distri = self._setuprun_define_analysis_window(
            stream, overall_distri, t_begin, t_end)
        if self.verbose:
            self.log(f'Global window bounds: {ft} to {lt}', level='verbose')
        self.run = PickerRunParameters(rea_name=rea_name,
                                       wavefile=full_wavefile,
                                       stream=stream,
                                       channel_maps=channel_maps,
                                       overall_distri=overall_distri,
                                       global_first_time=ft,
                                       global_last_time=lt,
                                       t_begin=t_begin,
                                       plotter=plotter)
        self.run.plotter.global_window_timebounds(self.run.global_first_time,
                                                  self.run.global_last_time,
                                                  sorted(self.run.stations))
        self.run.plotter.pick_window_setup(self.run.global_first_time,
                                           self.run.global_last_time,
                                           sorted(self.run.stations))

    def _setuprun_get_wavefile_name(self, rea_name):
        """
        Get the full WAV filename

        :param rea_name: full path to database file
        """
        # Pick_Function.m:103
        # full_name = os.path.join(self.rea_path, self.rea_name)
        if self.verbose:
            self.log(f'database filename = {rea_name}', level='verbose')
        cat, wav_names = read_nordic(rea_name, return_wavnames=True)
        assert len(wav_names) == 1, 'More than one wav_name in database file'
        parts = wav_names[0][0].split('-')
        full_wav_name = os.path.join(self.wav_base_path, parts[0], parts[1],
                                     wav_names[0][0])
        return full_wav_name

    def _setuprun_remove_problem_stations(self, stream, channel_maps, plotter,
                                          t_begin, n_smooth=15):
        """
        Remove flat-lined stations

        Finding the Global minimum on all the stations
        Filtering parameters + first window + smooth

        :param stream: all traces
        :param channel_maps: mapping of channel names to components
        :param plotter: the plotter object
        :param t_begin: global reference time for begin of traces
        :n_smooth: how many samples to smooth over for calculating Kurtosis
        :returns: overall_distribution of extrema, channel_maps, plotter
        """
        # Pick_Function.m:134
        p = self.param
        overall_distri = []
        plotter.global_window_setup()
        i_trace = 0
        rm_stations = []
        # for station, channel_map in sorted(channel_maps.items(),
        #                                    key=lambda x: x[0]):
        for station, channel_map in channel_maps.items():
            trace = stream.select(id=channel_map.Z)[0]
            trace_offset = trace.stats.starttime - t_begin
            sr = trace.stats.sampling_rate
            assert station == trace.stats.station,\
                'trace station ({}) != iteration station ({})'.format(
                    trace.stats.station, station)
            if np.all(np.diff(trace.data) == 0):
                self.log(f'Station {station} flat-lined, ignoring',
                         level='warning')
                rm_stations.append(station)
                continue
            # k = Kurtosis(p.gw_frequency_band, p.gw_sliding_length, n_smooth)
            # i_picks, k_picks = k.pick_trace(trace)
            kurto_cum, _, _ =\
                Kurtosis.trace2kurto(trace, p.gw_frequency_band,
                                     p.gw_sliding_length, n_smooth)
            if len(kurto_cum.data) == 0:
                warnings.warn('kurto_cum is empty, ignoring station "{}"'
                              .format(station))
                # REMOVE THIS STATION FROM KURTOSIS CALCULATION
                rm_stations.append(station)
                continue

            mean_kurto = mean_trace(kurto_cum)
            kurto_ext, ind_ext, _ =\
                Kurtosis.follow_extrem(mean_kurto, 'mini', p.gw_n_extrema,
                                       [p.gw_extrema_samples], 'no-normalize',
                                       'no-sense')
            ext_seconds = [trace_offset + i/sr for i in ind_ext]
            if self.debug:
                debug_stream = Stream([trace, mean_kurto, kurto_ext[0]])
                debug_stream[1].stats.channel = 'KUR'
                debug_stream[2].stats.channel = 'EXT'
                self.log(str(station, ind_ext, ext_seconds), level='debug')
                debug_stream.plot(equal_scale=False)
            overall_distri.extend(ext_seconds)
            plotter.global_window_onetrace(trace, i_trace, ext_seconds)
            i_trace += 1

        # REMOVE PROBLEM STATIONS (if necessary)
        channel_maps = {s: v for s, v in channel_maps.items()
                        if s not in rm_stations}
        return overall_distri, channel_maps, plotter

    def _setuprun_define_analysis_window(self, stream, overall_distri, t_begin,
                                         t_end):
        """
        Define size of new analysis window

        :param stream: stream containing all data traces
        :param overall_distri: array of extrema on all stations (seconds from
            t_begin)
        :param t_begin: reference starttime for all traces
        :param t_end: end of all traces
        :returns: first_time, last_time, overall_distri
        """
        # Pick_Function.m:204
        # max_offset is data length * gw_end_cutoff
        max_offset = self.param.gw_end_cutoff * (t_end - t_begin)
        # Cut down picks to those within global bounds
        overall_distri = [s for s in overall_distri if s <= max_offset]
        min_global = center_distri(overall_distri,
                                   self.param.gw_distri_secs)

        T_left = self.param.gw_offsets[0]
        T_right = self.param.gw_offsets[1]
        first_offset = min_global + T_left
        last_offset = min_global + T_right
        if first_offset < 0:
            first_offset = 0
        if last_offset >= max_offset:
            last_offset = max_offset

        return (t_begin + first_offset,
                t_begin + last_offset,
                overall_distri)

    def _calc_snr(self, datS_filtered, debug=False):
        """
        Calculate the signal-to-noise relation using the S-wave channel(s)

        Calculates energy first, then the SNR based on variations in energy
        :param datS_filtered: S-wave stream, filtered
        """
        # energy = sqrt(sum(filt**2, 2))
        # snr = snr_function(energy, rsample, param.SNR_wind(1),
        #                    param.SNR_wind(2))
        # filt=filterbutter(3, station_param.f_energy(1),
        #                   station_param.f_energy(2), rsample, datS)
        squared = datS_filtered.copy()
        for tr in squared:
            tr.data = np.power(tr.data, 2)
        stacked = squared.stack()
        assert len(stacked) == 1, 'stacked data has more than one trace!'
        stacked[0].data = np.power(stacked[0].data, 0.5)
        energy = stacked[0]
        snr = snr_function(energy,
                           self.param.SNR_noise_window,
                           self.param.SNR_signal_window)
        if debug:
            snr_dB = snr.copy()
            snr_dB.data = 20*np.log10(snr_dB.data)
            snr_dB.stats.channel = 'SDB'
            energy.stats.channel = 'NRG'
            snr.stats.channel = 'SNR'
            Stream([snr_dB, snr, energy]).plot(equal_scale=False)
        return snr, energy

    def _snr_trustworthy(self, snr, n_smooth=100, debug=False):
        """
        Check if the signal can be trusted or not.

        Considers the signal trustworthy if, within the global pick window,
        the SNR crosses (from below to above) the specified threshold at least
        once and no more than SNR_threshold_crossings times

        :param snr: signal-to-noise ratio trace
        :n_smooth: length of moving average filter to apply before analysis
        """
        # clear('C','ll','rate')
        # stations{iter}
        # Pick_Function.m:380
        snr_smooth = smooth_filter(snr, n_smooth)
        snr_smooth = snr_smooth.slice(self.run.global_first_time,
                              self.run.global_last_time)
        SNR_threshold = self._get_SNR_threshold(snr_smooth)
        sign_change = np.diff(np.sign(snr_smooth.data - SNR_threshold))
        SNR_crossings = len(sign_change[sign_change == 2])
        # print('SNR_threshold={}, SNR_crossings={}'.format(
        #         SNR_threshold, SNR_crossings), flush=True)
        if debug:
            snr_smooth.plot()
        return (SNR_crossings <= self.param.SNR_max_threshold_crossings
                and SNR_crossings > 0)

    def _get_SNR_threshold(self, snr_smooth):
        """
        Calculate the signal-to-noise threshold value

        Can be set as an absolute value or as a fraction of max(SNR) - 1,
        using the parameter SNR_threshold_parameter
        :param snr_smooth: trace of smoothed signal-to-noise ratio
        """
        # Pick_Function.m:382
        tp = self.param.SNR_threshold_parameter
        if (tp > 0 and tp <= 1):
            threshold = 1 + tp * (np.nanmax(snr_smooth.data) - 1)
            # print('tp={}, snr_max={}, snr_dB_max={}'.format(
            #       tp, np.nanmax(snr_smooth.data),
            #       np.nanmax(snr_smooth.data)))
        else:
            assert tp < 0, f'Illegal SNR_threshold_parameter value: {tp:g}'
            threshold = -tp
        # Minimum possible SNR threshold is the quality = '3' SNR
        if threshold < min(self.param.SNR_quality_thresholds):
            threshold = min(self.param.SNR_quality_thresholds)
        return threshold

    def _run_Kurtosis(self, snr, energy, debug=False):
        """
        calculate extrema and estimate P and S onsets using the Kurtosis

        :param snr: signal-to-noise trace
        :param energy: energy trace
        :returns: i_onsetP, i_onset_S, samples from start of trace
        """
        first_time, last_time = self._refine_pick_window(energy)
        # if self.debug:
        self.log('_run_Kurtosis: refined pick window = {}-{}'.format(
              first_time, last_time), level='debug')
        if len(self.loop.datP) > 1:
            warnings.warn('Only working on first trace in datP')
        all_mean_M, _ = Kurtosis.trace2FWkurto(
            self.loop.datP[0], self.loop.station_params.kurt_frequencies,
            self.loop.station_params.kurt_window_lengths, 1,
            first_time, last_time, debug=self.debug)
        if self.debug:
            self.log('plotting all_mean_M', level='debug')
            all_mean_M.plot()
        # Pick_Function.m:430
        kurto_modif, ind_ext, ext_value = Kurtosis.follow_extrem(
            all_mean_M, 'mini', self.loop.station_params.n_follow,
            self.loop.station_params.kurt_smoothing_sequence,
            'no-normalize', 'no-sense')
        self.run.plotter.station_window_add_snr_nrg(
            self.run.global_first_time, self.run.global_last_time,
            min(self.param.SNR_quality_thresholds), self.loop.datP[0],
            snr, energy, all_mean_M, kurto_modif)
        extrema = [{'i': i, 'snr': snr.data[i]} for i in ind_ext]
        # print(extrema)
        min_thresh = min(self.param.SNR_quality_thresholds)
        # print(self.param.SNR_quality_thresholds, min_thresh)
        extrema = [x for x in extrema if x['snr'] > min_thresh]
        if self.debug:
            self.log(f'_run_Kurtosis: extrema indices: {ind_ext}',
                     level='debug')
            print(f'extrema={extrema}', level='debug')
        if len(extrema) == 0:
            i_onset_P, i_onset_S = None, None
        else:
            self.run.plotter.station_window_add_extrema(self, extrema)
            i_onset_P, i_onset_S = self._calc_follows(extrema)
        return extrema, i_onset_P, i_onset_S

    def _refine_pick_window(self, energy):
        """
        Refine pick window if requested
        """
        # choose the first datP trace as the time and sr reference
        tr = self.loop.datP[0]
        sr = tr.stats.sampling_rate
        tr_start = tr.stats.starttime

        if self.loop.station_params.energy_window == 0:
            return (self.run.global_first_time,
                    self.run.global_last_time)

        energy_smooth = smooth_filter(energy, 50)
        energy_smooth = energy_smooth.slice(self.run.global_first_time,
                                            self.run.global_last_time)
        # print(energy_smooth, type(energy_smooth), energy_smooth.data)
        ind_max = np.nanargmax(energy_smooth.data)
        last_sample = ind_max.copy()
        max_kurto_wind = np.max(self.loop.station_params.kurt_window_lengths)
        max_precursor = np.floor(
            sr * (self.loop.station_params.energy_window + max_kurto_wind))
        first_sample = ind_max - max_precursor
        if first_sample < 0:
            first_sample = 0
            last_sample = max_precursor
        # Pick_Function.m:417
        return tr_start + first_sample/sr, tr_start + last_sample/sr

    def _polarity_analysis(self, extrema, datS_filtered):
        """
        Return P and S onsets from extrema based on signal polarity

        :param extrema: list of 1 or 2 dicts {'i': index, 'value'?: value}
        :param datS_filtered: stream of traces used for S picking, filtered
        :returns: i_onset_P, i_onset_S
        """
        # Pick_Function.m:543
        rectP, aziP, dipP = fast_polar_analysis([e['i'] for e in extrema],
                                                2, datS_filtered[0],
                                                datS_filtered[1],
                                                datS_filtered[2])
        # dip_rect = np.multiply(np.sin(np.abs(np.radians(dipP))), rectP)
        dipp = np.sin(np.abs(np.radians(dipP)))
        smooth_dipp = np.lfilter(np.ones(100) / 100, 1, dipp)
        smooth_rectilinP = np.lfilter(np.ones(100) / 100, 1, rectP)
        Drb = np.sign(1.3 * smooth_dipp - smooth_rectilinP)
        DR = np.lfilter(np.ones(200) / 200, 1, np.multiply(rectP, Drb))
        DR[rectP == 0] = 0
        if len(extrema) == 0:
            return None, None
        else:
            i_onset_P, i_onset_S = None, None
            if len(extrema) == 1:
                mat_DR = DR(np.arange(np.floor(extrema[0]['i']) - 200,
                                      np.floor(extrema[0]['i']) + 200))
                tmp = np.max(np.abs(mat_DR)) * np.sign(np.mean(mat_DR))
                if tmp >= self.param.dip_rect_thresholds['P']:
                    i_onset_P = extrema[0]['i']
                elif tmp <= self.param.dip_rect_thresholds['S']:
                    i_onset_S = extrema[0]['i']
            elif len(extrema) == 2:
                mat_DR1 = DR(np.arange(np.floor(extrema[0]['i']) - 200,
                                       np.floor(extrema[0]['i'] + 200)))
                mat_DR2 = DR(np.arange(np.floor(extrema[1]['i']) - 200,
                                       np.floor(extrema[1]['i'] + 200)))
                if np.mean(mat_DR1) > np.mean(mat_DR2):
                    i_onset_P = extrema[0]['i']
                    i_onset_S = extrema[1]['i']
        return i_onset_P, i_onset_S

    def _calc_follows(self, extrema):
        """
        returns offsets depending on number of extrema to follow

        :param extrema: list of {'snr': 'i':} dicts
        :returns i_onset_P, i_onset_S
        :rtype: int, int
        """
        # Pick_Function.m:507
        # eliminate extrema whose snr is less than SNR_thresh
        n_follow = self.loop.station_params.n_follow
        assert n_follow in (1, 2), 'n_follow is not 1 or 2'
        if len(extrema) == 0:
            return None, None
        if n_follow == 1:
            return extrema[0]['i'], None
        else:
            self.log(extrema, level='debug')
            extrema.sort(key=lambda x: x['i'])
            if len(extrema) == 1:
                return extrema[0]['i'], None
            else:
                return extrema[0]['i'], extrema[1]['i']

    def _make_picks(self, i_onset_P, i_onset_S, snr):
        """
        Put onset_P and onset_S into list of obspy Picks
        """
        picks = []
        waveid = self.loop.datP[0].get_id()
        if i_onset_P is not None:
            picks.append(obspy_Pick(
                time=self.loop.index_to_time(i_onset_P),
                time_errors=self._SNR_to_time_error(snr.data[i_onset_P], 'P'),
                waveform_id=WaveformStreamID(
                    seed_string=self.loop.channel_map.P_write_cmp),
                phase_hint=self.loop.channel_map.P_write_phase,
                evaluation_mode='automatic',
                evaluation_status='preliminary'))

        if i_onset_S is not None:
            picks.append(obspy_Pick(
                time=self.loop.index_to_time(i_onset_S),
                time_errors=self._SNR_to_time_error(snr.data[i_onset_S], 'S'),
                waveform_id=WaveformStreamID(
                    seed_string=self.loop.channel_map.S_write_cmp),
                phase_hint=self.loop.channel_map.S_write_phase,
                evaluation_mode='automatic',
                evaluation_status='preliminary'))
        return picks

    def _calc_amplitude(self, picks, snr):
        """
        Calculate maximum Woods-Anderson amplitude

        :param picks: list of obspy picks for this station
        :param snr: trace containing signal-to-noise ratio
        :returns: obspy.core.event.magnitude.Amplitude
        """
        if len(picks) == 0:
            return None
        assert isinstance(picks[0], obspy_Pick),\
            f'picks[0] is a {type(picks[0])}, not a Pick'
        pick_P = [x for x in picks if x.phase_hint[0] == 'P']
        pick_S = [x for x in picks if x.phase_hint[0] == 'S']

        # Pick_Function.m:621
        # Look for response file in local directory, then data/CAL directory
        # Should take advantage of obspy to read all standard formats
        filename = self.loop.station_params.response_file
        if not os.path.isfile(filename):
            cal_dir = os.path.join(self.param.Main_path, 'CAL/')
            filename = os.path.join(cal_dir, filename)
        paz = get_response(filename, self.param.response_file_type)
        paz_wa = _wood_anderson_paz()
        # Set gain to 1 so that output will be m/s, not volts
        # paz_wa.stage_gain = 1
        # want output in m?  should I set wa gain and sensitivity to 1?
        if self.loop.station_params.use_polarity:
            wood = self.loop.dat_noH.copy()
        else:
            wood = self.loop.datP.copy()
        for tr in wood:
            tr.simulate(paz_remove=paz, paz_simulate=paz_wa, water_level=60.0)
            # tr.data = simulate_seismometer(
            #    tr.detrend().data, tr.stats.sampling_rate, paz, paz_wa)
        if len(pick_S) > 0:
            pick = pick_S[0]
            Amp = pk2pk(wood, pick.time, before_pick=20, after_pick=10)
        if len(pick_P) > 0:
            pick = pick_P[0]
            Amp = pk2pk(wood, pick.time, before_pick=5, after_pick=30)
        else:
            return None
        amplitude = obspy_Amplitude(
                generic_amplitude=Amp['amplitude'],
                type='AML',
                # category='other', # ["point", "mean", "duration",
                #                      "period", "integral", "other"]
                unit='m/s',  # obspy.core.event.header.AmplitudeUnit,
                period=Amp['period'],
                pick_id=pick.resource_id,
                waveform_id=pick.waveform_id)
        return amplitude

    def _remove_unassociated(self, picks):
        """
        Very basic (clustering-based) pick selection by association

        DOESN'T add/subsitute in alternative picks for those thrown out
        DOESN'T use a velocity model, max depth and min distance to estimate
            the possible range of pick times
        """
        # Remove lines with arrival times = start_time
        # P_picked_cell=rm_cell_line(P_picked_cell[2] == 0,P_picked_cell)
        # S_picked_cell=rm_cell_line(S_picked_cell[2] == 0,S_picked_cell)

        picks = self._remove_unclustered(picks)
        if self.param.assoc_distri_min_values <= self.run.n_stations:
            picks = self._remove_badly_distributed(picks)
            picks = self._remove_bad_delays(picks)
        return picks

    def _remove_unclustered(self, picks):
        """
        Remove picks and reassign phases based on clustering

        Very basic association
        :param picks: list of Pick objects
        :returns: modified picks
        """
        # Pick_Function.m:746
        p_picks = [p for p in picks if p.phase_hint[0] == 'P']
        if len(p_picks) >= self.param.assoc_distri_min_values:
            p_picks = cluster_clean(self.param.assoc_cluster_window_P, p_picks)

        s_picks = [p for p in picks if p.phase_hint[0] == 'S']
        if len(s_picks) >= self.param.assoc_distri_min_values:
            s_picks = cluster_clean(self.param.assoc_cluster_window_S, s_picks)

        return p_picks + s_picks

    def _remove_badly_distributed(self, picks):
        """
        Remove picks that are well outside of pick distribution

        :param picks: input Picks
        """
        p_picks = [p for p in picks if p.phase_hint[0] == 'P']
        s_picks = [p for p in picks if p.phase_hint[0] == 'S']
        _, iP = clean_distri([x.time.timestamp for x in p_picks],
                             self.param.assoc_distri_nstd_picks,
                             'median',
                             self.param.assoc_distri_min_values)
        _, iS = clean_distri([x.time.timestamp for x in s_picks],
                             self.param.assoc_distri_nstd_picks,
                             'median',
                             self.param.assoc_distri_min_values)
        return [p_picks[i] for i in iP] + [s_picks[i] for i in iS]

    def _remove_bad_delays(self, picks):
        """
        Eliminate picks based on distribution of P-S delays
        """
        # Pick_Function.m:763
        matches = picks_matched_stations(picks)
        if len(matches) == 0:
            return picks
        delay_stations = [m['station'] for m in matches]
        delays = [m['pickS'].time - m['pickP'].time for m in matches]
        _, i_PS = clean_distri(delays,
                               self.param.assoc_distri_nstd_delays,
                               'median',
                               self.param.assoc_distri_min_values)
        # Include good delay stations AND non-delay stations
        good_delay = [delay_stations[i] for i in i_PS]
        bad_delay = [s for s in delay_stations if s not in good_delay]
        good_picks = [p for p in picks
                      if p.waveform_id.station_code not in bad_delay]
        return good_picks

    def _save_event(self, picks, amplitudes):
        """
        Save event to NORDIC file
        """
        # Replaces a large section from Pick_Function.m 840-887
        if len(picks) == 0:
            warnings.warn('No picks saved!')
            o_time = self.run.global_first_time
        else:
            o_time = estimate_origin_time(picks)
        event = obspy_Event(
            event_type='earthquake',
            picks=picks,
            origins=[obspy_Origin(time=o_time)],
            amplitudes=amplitudes)
        self.log(event, level='debug')
        cat = obspy_Catalog(events=[event])
        # How can I change uncertainties to "0", "1", "2", "3"?
        # By creating an associated arrival and setting it's time_weight
        # to the appropriate number (which makes no sense because
        # a weight of zero should have no importance!)
        output_dbfile = os.path.join(self.rea_path_out,
                                     os.path.basename(self.run.rea_name))
        cat.write(output_dbfile,
                  format='NORDIC',
                  evtype='L',
                  wavefiles=[self.run.wavefile],
                  high_accuracy=True)

    def _SNR_to_time_error(self, snr, phase='P'):
        """
        Return approximate pick time errors corresponding to SNR

        :param snr: pick signal-to-noise ratio
        :param phase:   'P' or 'S', errrors are 2* more for 'S'
        :returns: time_errors
        :rtype: obspy QuantityError
        """
        assert phase in 'PS', "phase '{phase}' not in 'PS'"
        sr = self.loop.datP[0].stats.sampling_rate
        if snr > self.param.SNR_quality_thresholds[3]:
            uncertainty = 2. / sr
        elif snr >= self.param.SNR_quality_thresholds[2]:
            uncertainty = 8. / sr
        elif snr >= self.param.SNR_quality_thresholds[1]:
            uncertainty = 32. / sr
        elif snr >= self.param.SNR_quality_thresholds[0]:
            uncertainty = 128. / sr
        else:
            uncertainty = 2000. / sr
        if phase == 'S':
            uncertainty *= 2.
        return QuantityError(uncertainty)

    def log(self, string, level="info"):
        """
        Prints a colorful and fancy string and logs the same string.

        :param level: the log level. 
            'info' gives a green output and no level text
            'debug' gives a magenta output and 'DEBUG' text
            Everything else gives a red color and level text.

        Copied from Lion Krischer's hypoDDpy
        """
        logging.info(string)
        # Info is green
        if level == "info":
            print(bcolors.BRIGHTGREEN + f">>> {string}" + bcolors.RESET)
        elif level=="debug":
            level = level.upper()
            print(bcolors.BRIGHTMAGENTA + f">>> {level}: {string}"
                  + bcolors.RESET)
        elif level=="verbose":
            level = level.upper()
            print(bcolors.BRIGHTYELLOW + f">>> {level}: {string}"
                  + bcolors.RESET)
        else:
            level = level.lower().capitalize()
            print(bcolors.BRIGHTRED + f">>> {level}: {string}" + bcolors.RESET)
            # print("\033[1;31m" + ">>> " + level + ": " + string + "\033[1;m")
        sys.stdout.flush()

class bcolors:
    RED = '\033[30m'
    BRIGHTRED = '\033[1;31m'
    BRIGHTGREEN = '\033[0;32m'
    BRIGHTYELLOW = '\033[1;33m'
    BRIGHTBLUE = '\033[1;34m'
    BRIGHTMAGENTA = '\033[1;35m'
    BRIGHTCYAN = '\033[1;36m'
    BRIGHTWHITE = '\033[1;37m'
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    RESET = '\033[0m'
    ENDC = '\033[1;m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    
def center_distri(v, win_size, n_steps=1000):
    """
    Return the center of the window containing the most values in an array

    :param v: array of values
    :param win_size: window size
    :param n_steps: number of values between min(v) and max(v) to test
    :returns: center of the distribution
    """
    if len(v) == 0:
        return []

    t = []
    s = []
    v_min, v_max = np.min(v), np.max(v)
    for tv in np.linspace(v_min, v_max, n_steps):
        a = tv - win_size / 2
        b = tv + win_size / 2
        in_range = np.logical_and(v > a, v < b)
        n_in_range = len(np.nonzero(in_range)[0])
        t.append(n_in_range)
        s.append(tv)
    return s[np.argmax(t)]


def estimate_origin_time(picks, vp_over_vs=1.7):
    """
    estimate EQ origin time based on pick times

    Uses P-S delays if possible. If not, return the earliest pick
    :param picks: list of obspy Pick objects
    :param vp_over_vs: assumed velocity ratio
    """
    origin_time = _average_ps_o_time(picks, vp_over_vs)
    if origin_time is not None:
        return origin_time
    else:
        times = list(sorted([p.time for p in picks]))
        # print(f'times={times}, picks={picks}', flush=True)
        return times[0]


def _average_ps_o_time(picks, vp_over_vs):
    """
    Find origin times for each P-S pair

    Uses the equation: o_time = p_time - (s_time - p_time)/(vp/vs - 1)
    :param picks: list of obspy Pick objects
    :param vp_over_vs: assumed velocity ratio
    """
    o_times = []
    ps_delays, p_times = picks_ps_times(picks)
    if ps_delays is None:
        return None
    for ps, p in zip(ps_delays, p_times):
        o_times.append(p - ps/(vp_over_vs - 1))
    # Throw out values more than 3 std away
    zs = np.abs(stats.zscore([x.timestamp for x in o_times]))
    mean_timestamp = np.mean([o.timestamp for o, z in zip(o_times, zs) if z < 3])
    return UTCDateTime(mean_timestamp)


def _wood_anderson_paz():
    """
    Return PoleZerosStage for Wood-Anderson seismometer
    """
    return {'gain': 1.0,
            'poles': [(-6.283-4.7124j), (-6.283+4.7124j)],
            'sensitivity': 2080,
            'zeros': [0 + 0j]}
    # return PolesZerosResponseStage(
    #     pz_transfer_function_type='LAPLACE (RADIANS)',
    #     stage_gain=2800,
    #     stage_gain_frequency=10,
    #     zeros=[0],
    #     poles=[-6.2832-4.7124j, -6.2832+4.7124j],
    #     normalization_frequency=10,
    #     stage_sequence_number=1,
    #     input_units='m/s',
    #     output_units='counts')


if __name__ == '__main__':
    pass
