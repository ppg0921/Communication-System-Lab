function z = helperAdsbRxPhyInterpolator(y, adsbParam)
%helperAdsbRxPhyInterpolator ADS-B receiver interpolator
%   Z = helperAdsbRxPhyInterpolator(Y,ADSB) interpolates the received
%   signal, Y, to achieve an integer number of samples per chip. ADSB is a
%   structure that contains the configuration information for the ADS-B
%   receiver.
%
%   See also ADSBExample, helperAdsbRxPhy.

%   Copyright 2015-2016 The MathWorks, Inc.

%#codegen

coder.inline('never')

persistent interpFil

if isempty(interpFil)
  interpFil = dsp.FIRInterpolator(adsbParam.InterpolationFactor, ...
    adsbParam.InterpolationFilterCoefficients);
end

if adsbParam.InterpolationFactor > 1
  % Interpolate the input signal. For example, if Y is sampled at 2.4MHz,
  % since Mode-S signals are 2 MHz pulses, we need to upsample to a multiple
  % of 2 MHz. An interpolation factor of 5 will give us 12 MHz signal, which
  % is convenient for demodulation.
  z = interpFil(y);
else
  z = y;
end

