function address = helperFindRTLSDRRadio()
%helperFindRTLSDRRadio Find an RTL-SDR radio on the host computer
%   R = helperFindRTLSDRRadio searches the host computer 

%   Copyright 2016-2022 The MathWorks, Inc.

% First check if the HSP exists
if ~exist('sdrrroot', 'file')
  link = sprintf('<a href="https://www.mathworks.com/hardware-support/rtl-sdr.html">RTL-SDR Support From Communications Toolbox</a>');
  error('Unable to find Communications Toolbox Support Package for RTL-SDR Radio. To install the support package, visit %s', link);
end

try
  rtlsdrRadios = sdrrinfo();
catch
  rtlsdrRadios = {};
end

radioCnt = length(rtlsdrRadios);
address = cell(radioCnt,1);
for p=1:length(rtlsdrRadios)
  radioCnt = radioCnt + 1;
  address{p} = rtlsdrRadios(p).RadioAddress;
end
