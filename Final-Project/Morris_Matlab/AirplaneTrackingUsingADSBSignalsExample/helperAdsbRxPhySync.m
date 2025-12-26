classdef (StrictDefaults)helperAdsbRxPhySync < matlab.System
  %helperAdsbRxPhySync Mode-S packet synchronizer
  %   SYNC = helperAdsbRxPhySync creates an ADS-B receiver packet
  %   synchronizer System object that searches for Mode-S packets in the
  %   received signal and returns synchronized packet samples.
  %
  %   Step method syntax:
  %
  %   [PKT,CNT,ST] = step(PARSER,X) searches the received signal, X, for
  %   Mode-S packets. PKT is a matrix of received samples where each column
  %   is a Mode-S packet synchronized to the first modulated sample. CNT is
  %   the number of valid packets in PKT. ST is a vector of delay values in
  %   samples that represent the reception time of each packet in PKT with
  %   respect to the beginning of, X.
  %
  %   System objects may be called directly like a function instead of using
  %   the step method. For example, y = step(obj, x) and y = obj(x) are
  %   equivalent.
  %
  %   See also ADSBExample, helperAdsbRxPhy.
  
  %   Copyright 2015-2021 The MathWorks, Inc.
  
  %#codegen

  properties (Nontunable)
    ADSBParameters = helperAdsbConfig();
  end

  properties (Access = private)
    Buffer
    Filter
    zBuffer
  end

  methods
    function obj = helperAdsbRxPhySync(varargin)
      setProperties(obj,nargin,varargin{:},'ADSBParameters');
    end
  end

  methods(Access = protected)
    function setupImpl(obj,~)
      adsbParam  = obj.ADSBParameters;
      obj.Buffer = zeros( ...
        (adsbParam.SamplesPerFrame*adsbParam.InterpolationFactor) ...
        + adsbParam.MaxPacketLength, 1); % MaxPacketLength overlap
      obj.zBuffer = zeros( ...
        (adsbParam.SamplesPerFrame*adsbParam.InterpolationFactor) ...
        + adsbParam.MaxPacketLength, 1); % MaxPacketLength overlap
      obj.Filter = dsp.FIRFilter('Numerator',adsbParam.SyncFilter');
    end

    function [packetSamples,packetCnt,syncTimeVec, pktSig] = stepImpl(obj,x, z)
      adsbParam = obj.ADSBParameters;
      
      % Buffer signal to successfully process edge packets
      olapLength = adsbParam.MaxPacketLength;
      obj.Buffer(1:olapLength) = obj.Buffer((end-olapLength+1):end);
      obj.zBuffer(1:olapLength) = obj.zBuffer((end-olapLength+1):end);
      obj.Buffer((end-numel(x)+1):end) = x;
      obj.zBuffer((end-numel(z)+1):end) = z;
      xBuff = obj.Buffer;
      zBuff = obj.zBuffer;
      % Crosscorrelate with the sync sequence
      xFilt = obj.Filter(xBuff(1:adsbParam.SyncDownsampleFactor:end));
      
      % Search for packets
      [packetSamples,packetCnt,syncTimeVec, pktSig] = ...
        helperAdsbRxPhyPacketSearch(xFilt, xBuff,adsbParam, zBuff);

    end

    function s = saveObjectImpl(obj)
      % Set properties in structure s to values in object obj

      % Set public properties and states
      s = saveObjectImpl@matlab.System(obj);

      % Set private and protected properties
      s.Buffer = obj.Buffer;
      s.Filter = obj.Filter;
    end

    function loadObjectImpl(obj,s,wasLocked)
      % Set properties in object obj to values in structure s

      % Set private and protected properties
      if ~isequal(class(s.Buffer),'dsp.Buffer')
          % Preserve contents of saved Buffer property
          obj.Buffer = s.Buffer;
      else
          % Backward compatibility (saved before R2019b):
          % reset buffer to all zeros, not using dsp.Buffer
          adsbParam  = s.ADSBParameters;
          obj.Buffer = zeros( ...
              (adsbParam.SamplesPerFrame*adsbParam.InterpolationFactor) ...
              + adsbParam.MaxPacketLength, 1); % MaxPacketLength overlap
      end
      obj.Filter = s.Filter;

      % Set public properties and states
      loadObjectImpl@matlab.System(obj,s,wasLocked);
    end


    function [sz1,sz2,sz3] = getOutputSizeImpl(obj)
      % Return size for each output port
      adsbParam = obj.ADSBParameters;
      sz1 = [adsbParam.LongPacketLength adsbParam.MaxNumPacketsInFrame];
      sz2 = [1 1];
      sz3 = [adsbParam.MaxNumPacketsInFrame 1];
    end

    function [dt1,dt2,dt3] = getOutputDataTypeImpl(~)
      % Return data type for each output port
      dt1 = 'single';
      dt2 = 'double';
      dt3 = 'double';
    end

    function [cp1,cp2,cp3] = isOutputComplexImpl(~)
      % Return true for each output port with complex data
      cp1 = false;
      cp2 = false;
      cp3 = false;
    end

    function [fs1,fs2,fs3] = isOutputFixedSizeImpl(~)
      % Return true for each output port with fixed size
      fs1 = true;
      fs2 = true;
      fs3 = true;
    end
    
    function icon = getIconImpl(~)
      icon = sprintf('ADS-B\nPacket\nSynchronizer');
    end

    function name1 = getInputNamesImpl(~)
      name1 = 'x';
    end

    function [name1,name2,name3] = getOutputNamesImpl(~)
      % Return output port names for System block
      name1 = 'pkt';
      name2 = 'pktCnt';
      name3 = 'Tsync';
    end
  end

  methods(Static, Access = protected)
    function header = getHeaderImpl
      % Define header panel for System block dialog
      header = matlab.system.display.Header(mfilename('class'), ...
           'Title', 'Mode-S packet synchronizer');
    end
  end
end
