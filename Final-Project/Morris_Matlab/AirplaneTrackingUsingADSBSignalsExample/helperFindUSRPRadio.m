function address = helperFindUSRPRadio()
% helperFindUSRPRadio Find an USRP(TM) radio on the host computer

%   Copyright 2021-2024 The MathWorks, Inc.

% First check if the HSP exists
if ~exist('sdruroot', 'file')
  linkComms = sprintf('<a href="https://www.mathworks.com/hardware-support/usrp.html">USRP Support From Communications Toolbox</a>');
  linkWT = sprintf('<a href="https://www.mathworks.com/hardware-support/ni-usrp-radios.html">NI USRP Radio Support From Wireless Testbench</a>');
  error(['Unable to find required support package.\n\nTo install support for USRP N2xx or B2xx series radios, visit %s.\n\n' ...
      ' To install support for USRP E3xx, N3xx, X3xx, or X4xx series radios, visit %s'],linkComms,linkWT);
end

baseStruct = struct('Platform', '', 'IPAddress', '', 'SerialNum', '');

% Discover all USRP devices
rawDeviceList = getSDRuList();
if strcmp(rawDeviceList, 'No devices found')
    devices = baseStruct;
    if nargin == 0
        address = [];
        return
    end
	
else
    % Remove zeros from the end and use ',' as a token
    deviceList = [',' rawDeviceList(rawDeviceList~=0)];
    tokIdx = [strfind(deviceList, ',') length(deviceList)+1];
    devices = repmat(baseStruct, 1, (length(tokIdx)-1)/4);
    for p=1:(length(tokIdx)-1)/4
        devices(p).IPAddress = deviceList(tokIdx(4*p-3)+1:tokIdx(4*p-3+1)-1);
        if isempty(devices(p).IPAddress)
            devices(p).IPAddress = '';
        end
        typestr = deviceList(tokIdx(4*p-2)+1:tokIdx(4*p-2+1)-1);
        if strcmp(typestr, 'usrp2')
            devices(p).Platform = 'N200/N210/USRP2';
        else
            devices(p).Platform = deviceList(tokIdx(4*p)+1:tokIdx(4*p+1)-1);
        end
        if ~isempty(devices(p).Platform)
            devices(p).SerialNum = deviceList(tokIdx(4*p-1)+1:tokIdx(4*p-1+1)-1);
        end
        if strcmp(devices(p).Platform(1:2), 'B2') 
                address{p} = devices(p).SerialNum;
        else
                address{p} = devices(p).IPAddress;
        end
    end
end
