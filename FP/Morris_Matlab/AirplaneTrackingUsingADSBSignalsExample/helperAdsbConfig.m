function [adsbParam,sigSrc] = helperAdsbConfig(varargin)
%helperAdsbConfig ADS-B system parameters
%   P = helperAdsbConfig(UIN) returns ADS-B system parameters, P. UIN is
%   the user input structure returned by the helperAdsbUserInput function.
%
%   See also ADSBExample.

%   Copyright 2015-2024 The MathWorks, Inc.

% References: [1] Technical Provisions for Mode S Services and Extended
% Squitter, ICAO, Doc 9871, AN/464, First Edition, 2008.


symbolDuration       = 1e-6;             % seconds
chipsPerSymbol       = 2;
longPacketDuration   = 112e-6;           % seconds
shortPacketDuration  = 56e-6;            % seconds
preambleDuration     = 8e-6;             % seconds
gain                 = 60;               % radio gain in dB

if nargin == 0
    userInput.Duration = 10;
    userInput.FrontEndSampleRate = 2.4e6;
    userInput.RadioAddress = '0';
    userInput.SignalSourceType = ExampleSourceType.Captured;
    userInput.SignalFilename = 'adsb_capture_01.bb';
    userInput.launchMap = 0;
    userInput.logData = 0;
else
    tmp = varargin{1};
    if isstruct(tmp) || isa(tmp, 'ExampleController')
        userInput = varargin{1};    
    else
        userInput.Duration = 10;
        userInput.FrontEndSampleRate = tmp;
        userInput.RadioAddress = '0';
        userInput.SignalSourceType = ExampleSourceType.Captured;
        userInput.SignalFilename = 'adsb_capture_01.bb';
        userInput.launchMap = 0;
        userInput.logData = 0;
        if isstring(tmp) || ischar(tmp)
            % If platform is passed as the input, then it is considered as
            % the workflow for USRP in Simulink
            SDRuSimulinkPlatform = tmp;
        end
    end
end


% Simulink workflow for USRP

% Set some default ADSB parameters with respect to 'N200/N210/USRP2' USRP
% radio to maintain compatibility across all radio devices
% Platform = 'N200/N210/USRP2' Address = '192.168.10.2';
% Ignore these parameters for signal sources other than USRP radio in both
% MATLAB and Simulink workflows
adsbParam.MasterClockRate = 100e6;
adsbParam.DecimationFactor = 5;
if exist('SDRuSimulinkPlatform','var') == 1
    switch SDRuSimulinkPlatform
        case {'B200','B210','E320'}
            masterClockRate = 20e6;
            decimationFactor = 1;
        case {'N320/N321'}
            masterClockRate = 200e6;
            decimationFactor = 10;
        case {'X310','X300'}
            masterClockRate = 200e6;
            decimationFactor = 10;
            gain = 8;
        case {'N310','N300'}
            masterClockRate = 153.6e6;
            decimationFactor = 64;
        case {'N200/N210/USRP2'}
            masterClockRate = 100e6;
            decimationFactor = 5;
            gain = 25;
    end
    frontEndSampleRate = masterClockRate/decimationFactor;
    adsbParam.MasterClockRate = masterClockRate;
    adsbParam.DecimationFactor = decimationFactor;
end    

% Create signal source
switch userInput.SignalSourceType
    case ExampleSourceType.Captured          
        bbFileName = userInput.SignalFilename;
        if ~exist(bbFileName,"file")
          disp("Downloading captured ADSB data set.");
          fullFilePath = matlab.internal.examples.downloadSupportFile( ...
              "spc","ADSB/adsb_capture_01.zip");          
          unzip(fullFilePath);
          disp("Done.");
        end         
        sigSrc = comm.BasebandFileReader(bbFileName, 'CyclicRepetition', true);
        if exist('SDRuSimulinkPlatform','var') == 0
            frontEndSampleRate = sigSrc.SampleRate;
        end
        adsbParam.isSourceRadio = false;
        adsbParam.isSourcePlutoSDR = false;
    case ExampleSourceType.RTLSDRRadio
        frontEndSampleRate = 2.4e6;
        sigSrc = comm.SDRRTLReceiver(userInput.RadioAddress,...
            'CenterFrequency',1090e6,...
            'EnableTunerAGC',false,...
            'TunerGain',gain,...
            'SampleRate',frontEndSampleRate,...
            'OutputDataType','single',...
            'FrequencyCorrection',0);
        adsbParam.isSourceRadio = true;
        adsbParam.isSourcePlutoSDR = false;
    case ExampleSourceType.PlutoSDRRadio
        frontEndSampleRate = 12e6;
        sigSrc = sdrrx('Pluto', ...
            'CenterFrequency',1090e6, ...
            'GainSource', 'Manual', ...
            'Gain', gain, ...
            'BasebandSampleRate', frontEndSampleRate,...
            'OutputDataType','single');
        adsbParam.isSourceRadio = true;
        adsbParam.isSourcePlutoSDR = true;
    case ExampleSourceType.USRPRadio    
        radioDetails = findsdru();
        % Using a front end sample rate of 20e6 for most of USRP radios
        switch radioDetails(1).Platform
            case {'B200','B210','E320'}
                masterClockRate = 20e6;
                decimationFactor = 1;
            case {'N320/N321'}
                masterClockRate = 200e6;
                decimationFactor = 10;
            case {'X310','X300'}
                masterClockRate = 200e6;
                decimationFactor = 10;
                gain = 8;
            case {'N310','N300'}
                masterClockRate = 153.6e6;
                decimationFactor = 64;
            case {'N200/N210/USRP2'}
                masterClockRate = 100e6;
                decimationFactor = 5;
                gain = 25;
        end
        if strcmpi(radioDetails(1).Platform,'B200') || strcmpi(radioDetails(1).Platform,'B210')
            sigSrc = comm.SDRuReceiver( 'Platform', radioDetails(1).Platform,...
                'SerialNum', userInput.RadioAddress,...
                'CenterFrequency',1090e6, ...
                'GainSource', 'Property', ...
                'Gain', gain, ...
                'MasterClockRate', masterClockRate,...
                'DecimationFactor', decimationFactor,...
                'OutputDataType','double');
        else
            sigSrc = comm.SDRuReceiver( 'Platform', radioDetails(1).Platform,...
                'IPAddress', userInput.RadioAddress,...
                'CenterFrequency',1090e6, ...
                'GainSource', 'Property', ...
                'Gain', gain, ...
                'MasterClockRate', masterClockRate,...
                'DecimationFactor', decimationFactor,...
                'OutputDataType','double');
        end
        % We are using frontEndSampleRate of 20e6 for all the USRP radios
        % except for N310 or N300 where we are using 2.4e6 Hz.
        frontEndSampleRate = masterClockRate/decimationFactor;
        adsbParam.isSourceRadio = true;
        adsbParam.isSourcePlutoSDR = false;
        adsbParam.isSourceUSRPRadio = true;
        adsbParam.MasterClockRate = masterClockRate;
        adsbParam.DecimationFactor = decimationFactor;
    otherwise
        error('comm:examples:Exit', 'Aborted.');
end

adsbParam.FrontEndSampleRate = frontEndSampleRate;
adsbParam.Gain = gain;

% We need a sample rate of n*chipRate, where n > 2
chipRate = chipsPerSymbol/symbolDuration;
[n,d]=rat(frontEndSampleRate/chipRate);
if d>2
    interpRate = d;
else
    if n <= 1
        interpRate = 2*d;
    else
        interpRate = d;
    end
end

adsbParam.InterpolationFactor  = interpRate;
sampleRate = frontEndSampleRate * interpRate;
adsbParam.SampleRate = sampleRate;

adsbParam.SamplesPerSymbol = int32(sampleRate * symbolDuration);
adsbParam.SamplesPerChip = adsbParam.SamplesPerSymbol / chipsPerSymbol;
adsbParam.MaxPacketLength = ...
    int32((preambleDuration+longPacketDuration) ...
    * sampleRate);

% Calculate actual samples per frame based on the target number
maxNumLongPacketsInFrame  = 180;
maxPacketDuration = (preambleDuration+longPacketDuration);
maxPacketLength = maxPacketDuration*frontEndSampleRate;
adsbParam.SamplesPerFrame = maxNumLongPacketsInFrame * maxPacketLength;

% Estimate the number of packets we may receive in a frame. If the packets
% are received without any space in between them, we would get
% adsbParam.SamplesPerFrame/maxPacketLength number of packets, which is
% the absolute maximum. We will scale it by four.
adsbParam.MaxNumPacketsInFrame = floor(adsbParam.SamplesPerFrame ...
    / maxPacketLength / 4);

adsbParam.FrameDuration = adsbParam.SamplesPerFrame ...
    / frontEndSampleRate;

% Convert seconds to samples
adsbParam.LongPacketLength = ...
    int32(longPacketDuration*sampleRate);
adsbParam.PreambleLength = ...
    int32(preambleDuration*sampleRate);

% Convert seconds to bits
adsbParam.LongPacketNumBits  = ...
    int32(longPacketDuration / symbolDuration);
adsbParam.ShortPacketNumBits = ...
    int32(shortPacketDuration / symbolDuration);

b = rcosdesign(0.5, 3, double(adsbParam.SamplesPerChip));
adsbParam.InterpolationFilterCoefficients = single(b);

adsbParam.SyncSequence = [1 0 1 0 0 0 0 1 0 1 0 0 0 0 0 0];
adsbParam.SyncSequenceLength = length(adsbParam.SyncSequence);
adsbParam.SyncSequenceHighIndices = find(adsbParam.SyncSequence);
adsbParam.SyncSequenceNumHighValues = length(adsbParam.SyncSequenceHighIndices);
adsbParam.SyncSequenceLowIndices = find(~adsbParam.SyncSequence);
adsbParam.SyncSequenceNumLowValues = length(adsbParam.SyncSequenceLowIndices);
syncSignal = reshape(ones(adsbParam.SamplesPerSymbol/2,1)...
    *adsbParam.SyncSequence, 16*adsbParam.SamplesPerSymbol/2, 1);
adsbParam.SyncDownsampleFactor = 2;
adsbParam.SyncFilter = single(flipud(2*syncSignal(1:adsbParam.SyncDownsampleFactor:end)-1));

sigSrc.SamplesPerFrame = adsbParam.SamplesPerFrame;

end