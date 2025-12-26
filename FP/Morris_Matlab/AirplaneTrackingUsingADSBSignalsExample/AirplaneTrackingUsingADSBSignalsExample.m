%% Airplane Tracking Using ADS-B Signals
% This example shows how to track planes by processing automatic dependent
% surveillance-broadcast (ADS-B) signals. You can use previously captured
% signals or receive signals in real time using an RTL-SDR radio,
% ADALM-PLUTO radio or USRP(TM) radio. You can also visualize the tracked
% planes on a map using Mapping Toolbox(TM).

% Copyright 2015-2024 The MathWorks, Inc.

%% Required Hardware and Software
% By default, this example runs using previously captured data. Optionally,
% you can receive signals over-the-air. For this, you also need one of the
% following:
%
% * RTL-SDR radio and <https://www.mathworks.com/hardware-support/rtl-sdr.html _Communications
% Toolbox Support Package for RTL-SDR Radio_>.
% * Pluto radio and <https://www.mathworks.com/hardware-support/adalm-pluto-radio.html _Communications
% Toolbox Support Package for Analog Devices(R) ADALM-PLUTO Radio_>.
% * USRP N2xx or B2xx series radio and
% <https://www.mathworks.com/hardware-support/usrp.html _Communications
% Toolbox Support Package for USRP Radio_>. For information on supported
% radios, see <docid:usrpradio_ug#buzc7a6-1 _Supported Hardware and
% Required Software_>.
% * USRP E3xx, N3xx, or X3xx series radio and
% <https://www.mathworks.com/hardware-support/ni-usrp-radios.html _Wireless
% Testbench Support Package for NI USRP Radios_>. For information on
% supported radios, see
% <docid:wt_gs#mw_74eb94c7-dcbc-40dc-8a56-cc7bc0124002 _Supported Radio
% Devices_>.

%% Introduction
% ADS-B is a cooperative surveillance technology for tracking aircraft.
% This technology enables an aircraft to periodically broadcast its
% position information such as altitude, GPS coordinates, and heading, using the
% Mode-S signaling scheme.
%
% Mode-S is a type of aviation transponder interrogation mode. When an
% aircraft receives an interrogation request, it sends back the
% squawk code of the transponder. This is referred to as Mode 3A. Mode-S
% (Select) is another type of interrogation mode that is designed to help
% avoid interrogating the transponder too often. More details about Mode-S
% can be found in [ <#10 1> ]. This mode is widely adopted in Europe and is
% being phased in for North America.
%
% Mode-S signaling scheme uses squitter messages, which are defined as a
% non-solicited messages used in aviation radio systems. Mode-S has these
% attributes:
%
% * A transmit frequency of 1090 MHz
% * Pulse Position Modulation (PPM)
% * A data rate of 1 Mbit/s
% * A short squitter length of 56 microseconds
% * An extended squitter length of 112 microseconds
%
% Short squitter messages contain these fields:
%
% * Downlink Format (DF)
% * Capability (CA)
% * Aircraft ID, which comprises of a unique 24-bit sequence
% * CRC Checksum
%
% Extended squitter (ADS-B) messages contain all the information in a short
% squitter and one of these values:
%
% * Altitude
% * Position
% * Heading
% * Horizontal and Vertical Velocity
%
% The signal format of Mode-S has a sync pulse that is 8 microseconds long
% followed by 56 or 112 microseconds of data, as this figure shows.
%
% <<../sdrrModeSSignalFormat.png>>

%% Receiver Structure
% This block diagram summarizes the receiver code structure. The
% processing has four main parts: signal source, physical layer, message
% parser, and data viewer.
%
% <<../ADSBFlowDiagram.png>>

%%
% *Signal Source*
%
% You can specify one of these signal sources:
%
% 
% 
% * |''Captured Signal''| - Over-the-air signals written to a file and sourced
% from a Baseband File Reader object at 2.4 Msps
% * |''RTL-SDR Radio''| - RTL-SDR radio at 2.4 Msps
% * |''ADALM-PLUTO Radio''| - ADALM-PLUTO radio at 12 Msps
% * |''USRP Radio''| - USRP radio at 20 Msps for all radios, except
% N310/N300 series that uses 2.4 Msps
%
% If you set |''RTL-SDR''| or |''ADALM-PLUTO''| or |''USRP Radio''| as the
% signal source, the example searches your computer for the radio an 
% RTL-SDR radio at radio address '0' or an ADALM-PLUTO
% radio at radio address 'usb:0' and uses it as the signal source.
%
% The extended squitter message is 120 micro seconds long, so the
% signal source is configured to process enough samples to contain 180
% extended squitter messages simultaneously, and set |SamplesPerFrame| of the
% signal property accordingly. The rest of the algorithm searches for
% Mode-S packets in this frame of data and returns all correctly identified
% packets. This type of processing is reffered to as batch processing. An
% alternative approach is to process one extended squitter message at a
% time. This single packet processing approach incurs 180 times more
% overhead than the batch processing, while it has 180 times less delay.
% Since the ADS-B receiver is delay tolerant, you use batch processing 
% in this example.

%%
% *Physical Layer*
%
% The physical layer (PHY) processes the baseband samples from 
% the signal source to produce packets that contain the PHY layer header
% information and raw message bits. This diagram shows the
% physical layer structure.
%
% <<../ADSB_PHY.png>>
%
% The RTL-SDR radio can use a sampling rate in the range
% [200e3, 2.8e6] Hz. When the source is an RTL-SDR radio, the example uses a
% sampling rate of 2.4 MHz and interpolates by a factor of 5 to a
% practical sampling rate of 12 MHz.
%
% The ADALM-PLUTO radio can use a sampling rate in the range
% [520e3, 61.44e6] Hz. When the source is an ADALM-PLUTO, the
% example samples the input directly at 12 MHz.
%
% The USRP radios are capable of using different sampling rates. When the
% USRP radio is the source, the example samples the input directly at
% 20 MHz sample rate for most of the radios. For the N310/N300 radio the
% data is received at 2.4 MHz sample rate and interpolates by a factor of 5
% to a practical sampling rate of 12e6.
%
% For example, if the data rate is 1 Mbit/s and the effective sampling rate
% is 12 MHz, the signal contains 12 samples per symbol. 
% The receive processing chain uses the magnitude of the complex symbols.
%
% The packet synchronizer works on subframes of data equivalent to two
% extended squitter packets, that is, 1440 samples at 12 MHz or 120 micro
% seconds. This subframe length ensures that the subframe contains whole 
% extended squitter. The packet synchronizer first
% correlates the received signal with the 8 microsecond preamble and finds
% the peak value. The synchronizer then validates the 
% synchronization point by checking if it matches the 
% preamble sequence, [1 0 0 0 0 0 1 0 1 0 0 0 0 0 0],
% where a value of 1 represents a high value and a value of 0 
% represents a low value.
%
% The Mode-S PPM scheme defines two symbols. Each symbol has two
% chips, where one has a high value and the other has a low value. If the
% first chip is high and the subsequent chip is low, the symbol
% is 1. Alternatively, if the first chip is low and the subsequent 
% chip is high chip, then the symbol is 0. 
% The bit parser demodulates the received chips and
% creates a binary message. A CRC checker then validates the binary
% message. The output of the bit parser is a 
% vector of Mode-S physical layer header packets that contain these fields:
%
% * RawBits - Raw message bits
% * CRCError - FALSE if CRC passes, TRUE if CRC fails
% * Time - Time of reception in seconds, from the start of reception
% * DF - Downlink format (packet type)
% * CA - Capability

%%
% *Message Parser*
%
% The message parser extracts data from the raw bits based on the packet
% type described in [ <#10 2> ]. This example can parse short squitter
% packets and extended squitter packets that contain airborne velocity,
% identification, and airborne position data.

%%
% *Data Viewer*
%
% The data viewer shows the received messages on a graphical user interface
% (GUI). For each packet type, the data viewer shows 
% the number of detected packets, the number
% of correctly decoded packets, and the packet error rate (PER).
% As the radio captures data, the application lists information 
% decoded from these messages in a table.

%% Track Airplanes Using ADS-B Signals
% The receiver prompts you for user input and initializes variables. 
% After you set the input values call the signal source, physical layer, 
% message parser, and data viewer in a loop. 
% The loop keeps track of the radio time using the frame duration.

% The default configuration runs using captured data. You can set
% |cmdlineInput| to |1|, then run the example to optionally change these
% configuration settings:
% # Reception duration in seconds,
% # Signal source (captured data or RTL-SDR radio or ADALM-PLUTO radio or USRP radio),
% # Optional output methods (map and/or text file).

% For the option to change default settings, set |cmdlineInput| to 1.
clc; close all; clear; clear helperAdsbRxPhy;

cmdlineInput = 1;
if cmdlineInput
    % Request user input from the command-line for application parameters
    userInput = helperAdsbUserInput;
else
    load('defaultinputsADSB.mat');
end

% userInput.SignalFilename = "C:\Users\user\Desktop\College Things\Senior 1\Comms Lab\FP\my_adsb_capture_10s.bb";

% Calculate ADS-B system parameters based on the user input
[adsbParam,sigSrc] = helperAdsbConfig(userInput);

% Create the data viewer object and configure based on user input
viewer = helperAdsbViewer('LogFileName',userInput.LogFilename, ...
    'SignalSourceType',userInput.SignalSourceType);
if userInput.LogData
    startDataLog(viewer);
end
if userInput.LaunchMap
    startMapUpdate(viewer);
end

% Create message parser object
msgParser = helperAdsbRxMsgParser(adsbParam);

% Start the viewer and initialize radio time
start(viewer)
radioTime = 0;

% Main loop
dopplerHist = [];
timeHist = [];
Fs = adsbParam.SampleRate;
dopplerDB = containers.Map('KeyType','char','ValueType','any');
rcv = [];

lastLat = NaN;
lastLon = NaN;
lastICAO = '';
last_packet = [];
valid_indices = [];
signal_for_analyze = [];
last_vel = 0;
last_heading = 0;

freq_change = containers.Map('KeyType', 'Char', 'ValueType', 'any');
aircrafts = containers.Map('KeyType', 'Char', 'ValueType', 'any');


while radioTime < userInput.Duration

    if adsbParam.isSourceRadio
        if adsbParam.isSourcePlutoSDR
            [rcv,~,lostFlag] = sigSrc();
        else
            [rcv,~,lost] = sigSrc();
            lostFlag = logical(lost);
        end
    else
        rcv = sigSrc();
        lostFlag = false;
    end

    % Process physical layer information (Physical Layer)
    [pkt,pktCnt,last_packet, pktSig] = helperAdsbRxPhy(rcv,radioTime,adsbParam);
    
    % Parse message bits (Message Parser)
    [msg,msgCnt] = msgParser(pkt,pktCnt);

    for k = 1:msgCnt
        if ~isempty(strtrim(msg(k, 1).ICAO24))
            lastICAO = msg(k, 1).ICAO24;
            if msg(k, 1).AirbornePosition.Longitude ~= 0 ...
               && msg(k, 1).AirbornePosition.Latitude ~= 0 ...
               && ~isnan(msg(k, 1).AirbornePosition.Latitude) ...
               && ~isnan(msg(k, 1).AirbornePosition.Latitude)
                lastLat = msg(k, 1).AirbornePosition.Latitude;
                lastLon = msg(k, 1).AirbornePosition.Longitude;
                updateAircraft(aircrafts, lastICAO, 'lat', lastLat, pktSig);
                updateAircraft(aircrafts, lastICAO, 'lon', lastLon, pktSig);
            
                if ~isempty(pktSig)
                    signal_for_analyze = pktSig;
                    if ~isKey(freq_change, lastICAO)
                        freq_change(lastICAO) = struct('freq',[]);
                    end
                    f_est = estimate_doppler(pktSig);
                    last_arr = freq_change(lastICAO).freq;
                    item = freq_change(lastICAO);
                    item.freq = [last_arr; f_est];
                end
            
            elseif msg(k, 1).AirborneVelocity.Speed ~= 0 ...
                    && msg(k, 1).AirborneVelocity.Heading ~= 0 ...
                    && ~isnan(msg(k, 1).AirborneVelocity.Speed) ...
                    && ~isnan(msg(k, 1).AirborneVelocity.Heading)
                    lastHeading = msg(k, 1).AirborneVelocity.Heading;
                    lastVelocity = msg(k, 1).AirborneVelocity.Speed;
                    updateAircraft(aircrafts, lastICAO, 'vel', lastVelocity, pktSig);
                    updateAircraft(aircrafts, lastICAO, 'heading', lastHeading, pktSig);
            end
        end
    end

    % View results packet contents (Data Viewer)
    update(viewer,msg,msgCnt,lostFlag);

    % Update radio time
    radioTime = radioTime + adsbParam.FrameDuration;
end

f_dopp = estimate_doppler(signal_for_analyze);

fprintf('    Last aircraft position - ICAO: %s, Lat: %.6f, Lon: %.6f\n', ...
        lastICAO, lastLat, lastLon);

Fs = 20e6;

figure;
plot(1:length(signal_for_analyze), signal_for_analyze);

figure;
signal_for_analyze = [zeros(1e6, 1); signal_for_analyze; zeros(1e6, 1)];
L = length(signal_for_analyze);
freq_axis = Fs/L*(-L/2:L/2-1);
plot(freq_axis, abs(fftshift(fft(signal_for_analyze))));

% Stop the viewer and release the signal source
stop(viewer)
release(sigSrc)


function f_est = estimate_doppler(x)
    Fs = 20e6;
    x = x(:);
    x = x ./ abs(x + eps);
    dphi = angle(conj(x(1:end-1)) .* x(2:end));
    f_est = mean(dphi) * Fs / (2*pi);
    disp(['f_est = ' num2str(f_est)]);
end

function aircrafts = updateAircraft(aircrafts, ICAO24, field, value, pktSig)
    arguments
        aircrafts
        ICAO24
        field
        value
        pktSig = []
    end

    if ~isKey(aircrafts, ICAO24)
        aircrafts(ICAO24) = struct('lat',[], 'lon',[], 'vel',[], 'heading',[]);
    end

    s = aircrafts(ICAO24);
    s.(field) = value;
    aircrafts(ICAO24) = s;

    % if isComplete(s)
    %     if isempty(pktSig)
    %         disp(["No corresponding signal provided"])
    %     else
    %         est_doppler(pktSig, s.lat, s.lon, s.vel, s.heading, ICAO24);
    %     end
    % end
end

function tf = isComplete(s)
    tf = ~isempty(s.lat) && ...
         ~isempty(s.lon) && ...
         ~isempty(s.vel) && ...
         ~isempty(s.heading);
end



%%
% This figure shows the information about the detected airplanes.
%
% <<../sdrrTrackedFlightsOnApp.png>>
%
% You can also observe the airplanes on a map if you have a
% Mapping Toolbox license.
%
% <<../sdrrFlightsOnMap.png>>

%% Further Exploration
% You can investigate ADS-B signals using the ADSBExampleApp app.
% Use this app to select the signal source and change the duration. To
% launch the app, enter |ADSBExampleApp| in the MATLAB Command Window.

%% References
% # International Civil Aviation Organization, Annex 10, Volume 4.
% Surveillance and Collision Avoidance Systems.
% # Technical Provisions For Mode S Services and Extended Squitter (Doc
% 9871)