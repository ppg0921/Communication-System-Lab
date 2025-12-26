function varargout = ADSBExampleApp
%ADSBExampleApp Further exploration of ADSB Example

% Copyright 2018-2024 The MathWorks, Inc.

% Top-level figure:
figureHandle = uifigure('Position', [100 100  1053, 525], ...
    'Visible', 'off', ...
    'HandleVisibility', 'on', ...
    'NumberTitle', 'off', ...
    'IntegerHandle', 'off', ...
    'MenuBar', 'none', ...
    'Name', 'Automatic Dependant Surveillance-Broadcast (ADS-B) Explorer', ...
    'Tag', 'ADSBMLAppFigure', ...
    'AutoResizeChildren', 'off');
movegui(figureHandle, 'center');

% Create the container for the controller and the viewer
controllerPanel = uicontainer(figureHandle, ...
  'Tag', 'ADSBMLAppCtrlPanel', ...
  'Units', 'pixels');
viewerPanel = uicontainer(figureHandle, ...
  'Tag', 'ADSBMLAppViewerPanel', ...
  'Units', 'pixels');

% Instantiate the viewer and controller
viewer = helperAdsbViewer('ParentHandle', viewerPanel, 'isInApp', true);
controller = helperAdsbController('ParentHandle', controllerPanel, ...
  'Viewer', viewer);
controllerPanel.Position = [0 0 263 525];
viewerPanel.Position = [263 0 789 525];

render(controller);

figureHandle.Visible = 'on';

if nargout > 0
    varargout{1} = controller;
end
if nargout > 1
    varargout{2} = viewer;
end

end