%% ADS-B Receiver with Doppler Shift Estimation
% Modified from AirplaneTrackingUsingADSBSignalsExample
% Adds Doppler shift estimation and radial velocity calculation
% Compatible with .bb format files

clearvars; close all; clc;

%% Configuration
% Your ground station location (Banqiao, Taipei)
rxLat = 25.017851;
rxLon = 121.544249;
rxAlt = 100; % Altitude in meters (adjust as needed)

% ADS-B parameters
fc = 1090e6; % Carrier frequency (Hz)
c = 299792458; % Speed of light (m/s)

%% User Input Configuration
cmdlineInput = 1; % Set to 1 to enable custom configuration

if cmdlineInput
    % Custom configuration for Doppler analysis
    userInput.Duration = 60; % Duration in seconds (adjust as needed)
    userInput.SignalSourceType = 'Captured';
    userInput.BBFilePath = 'your_recording.bb'; % CHANGE THIS to your .bb file path
    userInput.SampleRate = 2e6; % Sample rate in Hz - check your .bb file metadata
    userInput.LogData = true; % Log decoded messages
    userInput.LogFilename = 'adsb_doppler_log.txt';
    userInput.LaunchMap = false; % Set to true if you have Mapping Toolbox
    
    % Prompt for .bb file if not found
    if ~isfile(userInput.BBFilePath)
        [file, path] = uigetfile('*.bb', 'Select your ADS-B recording (.bb file)');
        if file ~= 0
            userInput.BBFilePath = fullfile(path, file);
        else
            error('No .bb file selected. Please specify the file path.');
        end
    end
else
    load('defaultinputsADSB.mat');
    % Override with .bb file configuration
    userInput.SignalSourceType = 'Captured';
    userInput.BBFilePath = 'your_recording.bb'; % CHANGE THIS
    userInput.SampleRate = 2e6;
end

%% Read .bb File Header and Data
fprintf('Reading .bb file: %s\n', userInput.BBFilePath);
[rxSignal, bbSampleRate, bbCenterFreq] = readBBFile(userInput.BBFilePath);

% Use sample rate from file if available, otherwise use configured value
if bbSampleRate > 0
    fs = bbSampleRate;
    fprintf('Using sample rate from .bb file: %.2f MHz\n', fs/1e6);
else
    fs = userInput.SampleRate;
    fprintf('Using configured sample rate: %.2f MHz\n', fs/1e6);
end

% Check if we need to resample to 2 MHz (ADS-B standard)
if fs ~= 2e6
    fprintf('Resampling from %.2f MHz to 2 MHz...\n', fs/1e6);
    [P, Q] = rat(2e6/fs);
    rxSignal = resample(rxSignal, P, Q);
    fs = 2e6;
end

% Limit duration if needed
maxSamples = userInput.Duration * fs;
if length(rxSignal) > maxSamples
    rxSignal = rxSignal(1:maxSamples);
end

fprintf('Signal loaded: %.2f seconds, %.2f million samples\n', ...
    length(rxSignal)/fs, length(rxSignal)/1e6);

%% Calculate ADS-B system parameters
adsbParam.SampleRate = fs;
adsbParam.FrameDuration = 0.25; % Process in 250ms chunks
adsbParam.SamplesPerFrame = adsbParam.FrameDuration * fs;
adsbParam.isSourceRadio = false; % We're using captured data
adsbParam.PreambleThreshold = 150;
adsbParam.MessageLength = 112; % bits

% Create signal source object for captured data
sigSrc = dsp.SignalSource(rxSignal, adsbParam.SamplesPerFrame);

%% Create the data viewer object
viewer = helperAdsbViewer('LogFileName', userInput.LogFilename, ...
    'SignalSourceType', userInput.SignalSourceType);
if userInput.LogData
    startDataLog(viewer);
end
if userInput.LaunchMap
    startMapUpdate(viewer);
end

%% Create message parser object
msgParser = helperAdsbRxMsgParser(adsbParam);

%% Initialize Doppler tracking structures
dopplerData = struct('ICAO', {}, 'Time', {}, 'Doppler', {}, ...
    'RadialVel', {}, 'Lat', {}, 'Lon', {}, 'Alt', {}, ...
    'Velocity', {}, 'Heading', {}, 'Range', {}, 'Bearing', {});
frameCount = 0;

%% Main Processing Loop
start(viewer)
radioTime = 0;

fprintf('\nProcessing ADS-B signal with Doppler estimation...\n');
fprintf('Time\tMessages\tAircraft\tDoppler Range\n');
fprintf('----\t--------\t--------\t-------------\n');

while radioTime < userInput.Duration && ~isDone(sigSrc)
    frameCount = frameCount + 1;
    
    % Get next frame
    rcv = sigSrc();
    lostFlag = false;
    
    % Validate received data
    if isempty(rcv) || all(isnan(rcv)) || all(rcv == 0)
        radioTime = radioTime + adsbParam.FrameDuration;
        continue;
    end
    
    % Remove any NaN or Inf values
    rcv(~isfinite(rcv)) = 0;
    
    % Estimate carrier frequency offset for this frame
    [cfo, cfoConfidence] = estimateCarrierOffset(rcv, fs);
    dopplerShift = cfo; % Doppler shift in Hz
    
    % Correct for frequency offset
    t = (0:length(rcv)-1)' / fs;
    rcvCorrected = rcv .* exp(-1i*2*pi*cfo*t);
    
    % Process physical layer information
    [pkt, pktCnt] = helperAdsbRxPhy(rcvCorrected, radioTime, adsbParam);
    
    % Parse message bits
    [msg, msgCnt] = msgParser(pkt, pktCnt);
    
    % Process messages and calculate Doppler
    if msgCnt > 0
        for idx = 1:msgCnt
            currentMsg = msg(idx);
            
            % Check if we have position information
            if isfield(currentMsg, 'Latitude') && ...
               isfield(currentMsg, 'Longitude') && ...
               ~isnan(currentMsg.Latitude) && ~isnan(currentMsg.Longitude)
                
                % Calculate geometric parameters
                [range, bearing] = calculateRangeBearing(...
                    rxLat, rxLon, rxAlt, ...
                    currentMsg.Latitude, currentMsg.Longitude, ...
                    currentMsg.Altitude);
                
                % Calculate expected Doppler if we have velocity info
                if isfield(currentMsg, 'Velocity') && ...
                   isfield(currentMsg, 'Heading') && ...
                   ~isnan(currentMsg.Velocity) && ~isnan(currentMsg.Heading)
                    
                    % Calculate radial velocity component
                    relativeHeading = currentMsg.Heading - bearing;
                    radialVel = currentMsg.Velocity * cosd(relativeHeading);
                    
                    % Calculate expected Doppler
                    expectedDoppler = -(radialVel / c) * fc;
                else
                    % Estimate radial velocity from measured Doppler
                    radialVel = -(dopplerShift * c) / fc;
                    expectedDoppler = dopplerShift;
                    
                    % Set defaults if not available
                    if ~isfield(currentMsg, 'Velocity')
                        currentMsg.Velocity = NaN;
                    end
                    if ~isfield(currentMsg, 'Heading')
                        currentMsg.Heading = NaN;
                    end
                end
                
                % Store Doppler data
                dopplerData(end+1).ICAO = currentMsg.ICAO24;
                dopplerData(end).Time = radioTime;
                dopplerData(end).Doppler = dopplerShift;
                dopplerData(end).RadialVel = radialVel;
                dopplerData(end).Lat = currentMsg.Latitude;
                dopplerData(end).Lon = currentMsg.Longitude;
                dopplerData(end).Alt = currentMsg.Altitude;
                dopplerData(end).Velocity = currentMsg.Velocity;
                dopplerData(end).Heading = currentMsg.Heading;
                dopplerData(end).Range = range;
                dopplerData(end).Bearing = bearing;
            end
        end
    end
    
    % Update viewer
    update(viewer, msg, msgCnt, lostFlag);
    
    % Update radio time
    radioTime = radioTime + adsbParam.FrameDuration;
    
    % Progress update every 10 frames (2.5 seconds)
    if mod(frameCount, 10) == 0
        uniqueAircraft = length(unique({dopplerData.ICAO}));
        if ~isempty(dopplerData)
            dopplerRange = [min([dopplerData.Doppler]), max([dopplerData.Doppler])];
            fprintf('%.1fs\t%d\t\t%d\t\t[%.1f, %.1f] Hz\n', ...
                radioTime, length(dopplerData), uniqueAircraft, ...
                dopplerRange(1), dopplerRange(2));
        else
            fprintf('%.1fs\t0\t\t0\t\tNo data yet\n', radioTime);
        end
    end
end

%% Cleanup
stop(viewer)
release(sigSrc)

fprintf('\nProcessing complete!\n');
fprintf('Total Doppler measurements: %d\n', length(dopplerData));
fprintf('Unique aircraft tracked: %d\n', length(unique({dopplerData.ICAO})));

%% Visualize Results
if ~isempty(dopplerData)
    fprintf('\nGenerating visualizations...\n');
    visualizeDopplerResults(dopplerData, rxLat, rxLon);
    
    % Generate summary statistics
    generateDopplerSummary(dopplerData);
else
    fprintf('\nNo position data decoded. Check:\n');
    fprintf('1. Signal strength and quality\n');
    fprintf('2. Center frequency (should be 1090 MHz)\n');
    fprintf('3. Recording duration (need at least 30 seconds)\n');
end

%% Helper Functions

function [signal, sampleRate, centerFreq] = readBBFile(filename)
    % Read .bb file format (commonly used by SDR# and other SDR software)
    % .bb files contain 32-bit float IQ samples
    
    fid = fopen(filename, 'rb');
    if fid == -1
        error('Cannot open file: %s', filename);
    end
    
    % Try to read header if present (some .bb files have metadata)
    % Most .bb files are raw IQ data without header
    % Read first few bytes to check for header magic
    magicBytes = fread(fid, 4, 'uint8');
    
    % Check for common SDR# .bb format header
    if isequal(magicBytes, [0x62, 0x62, 0x00, 0x00]') % "bb" header
        % Has header - read metadata
        centerFreq = fread(fid, 1, 'uint32');
        sampleRate = fread(fid, 1, 'uint32');
        % Skip rest of header (usually 32 bytes total)
        fseek(fid, 32, 'bof');
    else
        % No header - assume defaults
        centerFreq = 1090e6; % ADS-B frequency
        sampleRate = 0; % Unknown, will use user-specified
        fseek(fid, 0, 'bof'); % Reset to beginning
    end
    
    % Read IQ data as 32-bit float pairs
    data = fread(fid, [2, inf], 'float32');
    fclose(fid);
    
    if isempty(data)
        error('No data read from file. Check file format.');
    end
    
    % Convert to complex signal (I + jQ)
    signal = complex(data(1,:)', data(2,:)');
    
    % Remove DC offset
    signal = signal - mean(signal);
    
    % Normalize
    signal = signal / max(abs(signal)) * 0.9;
    
    fprintf('.bb file info:\n');
    fprintf('  Samples: %d (%.2f MB)\n', length(signal), length(signal)*8/1e6);
    if sampleRate > 0
        fprintf('  Sample Rate: %.2f MHz\n', sampleRate/1e6);
        fprintf('  Duration: %.2f seconds\n', length(signal)/sampleRate);
    end
    fprintf('  Center Freq: %.2f MHz\n', centerFreq/1e6);
end

function [cfo, confidence] = estimateCarrierOffset(signal, fs)
    % Estimate carrier frequency offset using multiple methods
    
    % Check for invalid data
    if isempty(signal) || all(isnan(signal)) || all(signal == 0)
        cfo = 0;
        confidence = 0;
        return;
    end
    
    % Remove NaN and Inf values
    signal = signal(isfinite(signal));
    
    if length(signal) < 1000
        cfo = 0;
        confidence = 0;
        return;
    end
    
    % Remove DC component
    signal = signal - mean(signal);
    
    % Method 1: FFT-based coarse estimation
    N = min(length(signal), 50000);
    sig = signal(1:N);
    
    % Additional safety check
    if any(isnan(sig)) || any(isinf(sig))
        sig = sig(isfinite(sig));
        if length(sig) < 1000
            cfo = 0;
            confidence = 0;
            return;
        end
    end
    
    % Compute power spectrum
    nfft = 2048;
    try
        [pxx, f] = pwelch(sig, hann(nfft), nfft/2, nfft, fs, 'centered');
    catch
        % If pwelch fails, return zero offset
        cfo = 0;
        confidence = 0;
        return;
    end
    
    % Find peak (excluding DC region)
    dcExclude = abs(f) < 500; % Exclude ±500 Hz around DC
    pxx(dcExclude) = min(pxx);
    [maxPower, maxIdx] = max(pxx);
    cfo = f(maxIdx);
    
    % Calculate confidence based on peak-to-average ratio
    avgPower = mean(pxx);
    confidence = maxPower / avgPower;
    
    % Limit to reasonable aircraft Doppler range
    % Max Doppler for aircraft: ~±5 kHz at 1090 MHz
    cfo = max(min(cfo, 5000), -5000);
end

function [range, bearing] = calculateRangeBearing(lat1, lon1, alt1, lat2, lon2, alt2)
    % Calculate range and bearing between two points
    
    % Convert to radians
    lat1 = deg2rad(lat1);
    lon1 = deg2rad(lon1);
    lat2 = deg2rad(lat2);
    lon2 = deg2rad(lon2);
    
    % Earth radius
    R = 6371000; % meters
    
    % Calculate horizontal distance using haversine formula
    dlat = lat2 - lat1;
    dlon = lon2 - lon1;
    a = sin(dlat/2)^2 + cos(lat1) * cos(lat2) * sin(dlon/2)^2;
    d_horiz = 2 * R * atan2(sqrt(a), sqrt(1-a));
    
    % Include altitude difference
    d_vert = alt2 - alt1;
    range = sqrt(d_horiz^2 + d_vert^2);
    
    % Calculate bearing
    y = sin(dlon) * cos(lat2);
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon);
    bearing = atan2d(y, x);
    if bearing < 0
        bearing = bearing + 360;
    end
end

function visualizeDopplerResults(dopplerData, rxLat, rxLon)
    % Create comprehensive visualizations for presentation
    
    uniqueICAO = unique({dopplerData.ICAO});
    numAircraft = length(uniqueICAO);
    colors = lines(numAircraft);
    
    % Create figure with subplots
    figure('Position', [50 50 1400 900], 'Name', 'ADS-B Doppler Analysis');
    
    % Plot 1: Aircraft tracks
    subplot(2,3,1);
    hold on; grid on;
    plot(rxLon, rxLat, 'r^', 'MarkerSize', 15, 'LineWidth', 2, ...
        'DisplayName', 'Receiver');
    
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        lats = [data.Lat];
        lons = [data.Lon];
        plot(lons, lats, 'o-', 'Color', colors(i,:), 'LineWidth', 1.5, ...
            'MarkerSize', 4, 'DisplayName', uniqueICAO{i});
    end
    xlabel('Longitude (°)');
    ylabel('Latitude (°)');
    title('Aircraft Tracks');
    legend('Location', 'best', 'FontSize', 8);
    
    % Plot 2: Doppler shift over time
    subplot(2,3,2);
    hold on; grid on;
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        times = [data.Time];
        dopplers = [data.Doppler];
        plot(times, dopplers, 'o-', 'Color', colors(i,:), 'LineWidth', 1.5, ...
            'MarkerSize', 4);
    end
    xlabel('Time (s)');
    ylabel('Doppler Shift (Hz)');
    title('Measured Doppler Shift');
    
    % Plot 3: Radial velocity
    subplot(2,3,3);
    hold on; grid on;
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        times = [data.Time];
        radVels = [data.RadialVel];
        plot(times, radVels, 'o-', 'Color', colors(i,:), 'LineWidth', 1.5, ...
            'MarkerSize', 4);
    end
    xlabel('Time (s)');
    ylabel('Radial Velocity (m/s)');
    title('Radial Velocity (+ = approaching)');
    
    % Plot 4: Range vs Time
    subplot(2,3,4);
    hold on; grid on;
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        times = [data.Time];
        ranges = [data.Range] / 1000; % Convert to km
        plot(times, ranges, 'o-', 'Color', colors(i,:), 'LineWidth', 1.5, ...
            'MarkerSize', 4);
    end
    xlabel('Time (s)');
    ylabel('Range (km)');
    title('Aircraft Range');
    
    % Plot 5: Doppler vs Range
    subplot(2,3,5);
    hold on; grid on;
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        ranges = [data.Range] / 1000;
        dopplers = [data.Doppler];
        plot(ranges, dopplers, 'o', 'Color', colors(i,:), 'MarkerSize', 6);
    end
    xlabel('Range (km)');
    ylabel('Doppler Shift (Hz)');
    title('Doppler vs Range');
    
    % Plot 6: Altitude profile
    subplot(2,3,6);
    hold on; grid on;
    for i = 1:numAircraft
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        times = [data.Time];
        alts = [data.Alt];
        plot(times, alts, 'o-', 'Color', colors(i,:), 'LineWidth', 1.5, ...
            'MarkerSize', 4);
    end
    xlabel('Time (s)');
    ylabel('Altitude (m)');
    title('Aircraft Altitude');
end

function generateDopplerSummary(dopplerData)
    % Generate summary statistics for presentation
    
    fprintf('\n=== DOPPLER ANALYSIS SUMMARY ===\n\n');
    
    uniqueICAO = unique({dopplerData.ICAO});
    
    for i = 1:length(uniqueICAO)
        mask = strcmp({dopplerData.ICAO}, uniqueICAO{i});
        data = dopplerData(mask);
        
        fprintf('Aircraft: %s\n', uniqueICAO{i});
        fprintf('  Observations: %d\n', length(data));
        fprintf('  Doppler shift: %.1f to %.1f Hz (mean: %.1f Hz)\n', ...
            min([data.Doppler]), max([data.Doppler]), mean([data.Doppler]));
        fprintf('  Radial velocity: %.1f to %.1f m/s (mean: %.1f m/s)\n', ...
            min([data.RadialVel]), max([data.RadialVel]), mean([data.RadialVel]));
        fprintf('  Range: %.1f to %.1f km\n', ...
            min([data.Range])/1000, max([data.Range])/1000);
        
        % Check if velocity data is available
        validVel = ~isnan([data.Velocity]);
        if any(validVel)
            fprintf('  Ground speed: %.1f m/s (%.1f knots)\n', ...
                mean([data(validVel).Velocity]), ...
                mean([data(validVel).Velocity]) / 0.514444);
        end
        fprintf('\n');
    end
    
    % Overall statistics
    fprintf('Overall Statistics:\n');
    fprintf('  Total measurements: %d\n', length(dopplerData));
    fprintf('  Doppler range: %.1f to %.1f Hz\n', ...
        min([dopplerData.Doppler]), max([dopplerData.Doppler]));
    fprintf('  Max radial velocity: %.1f m/s (%.1f knots)\n', ...
        max(abs([dopplerData.RadialVel])), ...
        max(abs([dopplerData.RadialVel])) / 0.514444);
end