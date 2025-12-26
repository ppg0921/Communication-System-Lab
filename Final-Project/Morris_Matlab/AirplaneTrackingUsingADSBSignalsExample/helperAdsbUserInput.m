function userInput = helperAdsbUserInput
%helperAdsbUserInput Gather user input for Airplane Tracking Example
%   [SRC,UIN] = helperAdsbUserInput displays questions on the MATLAB command
%   window and collects user input, UIN. SRC contains a source object,
%   which can be one of the following signal sources
%
%   Captured Signal (comm.BasebandFileReader)
%   RTL-SDR Radio (comm.SDRRTLReceiver, requires RTL-SDR Support Package
%                  for Communications Toolbox)
%
%   UIN is a structure of user inputs with following fields:
%
%   * Duration:         Run time of example
%   * RadioSampleRate:  Signal source sample rate
%   * RadioAddress:     Address string for radio (if radio is selected)
%   * SourceType:       Source type
%   * launchMap:        Flag to launch map at the start of example
%   * logData:          Flag to start logging at the start of example
%
%   See also ADSBExample

%   Copyright 2015-2020 The MathWorks, Inc.

controller = helperAdsbController;

userInput = getUserInput(controller);
