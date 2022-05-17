import torch.nn as nn


class SELayer(nn.Module):
    def __init__(self, c, r=4, use_max_pooling=False):
        super().__init__()
        self.squeeze = nn.AdaptiveAvgPool1d(1) if not use_max_pooling else nn.AdaptiveMaxPool1d(1)
        self.excitation = nn.Sequential(
            nn.Linear(c, c // r, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(c // r, c, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        bs, s, h = x.shape
        y = self.squeeze(x).view(bs, s)
        y = self.excitation(y).view(bs, s, 1)
        return x * y.expand_as(x)


class MlpBlock(nn.Module):
    def __init__(self, mlp_hidden_dim, mlp_input_dim, mlp_bn_dim, activation='gelu', regularization=0):
        super().__init__()
        self.mlp_hidden_dim = mlp_hidden_dim
        self.mlp_input_dim = mlp_input_dim
        self.mlp_bn_dim = mlp_bn_dim
        self.fc1 = nn.Linear(self.mlp_input_dim, self.mlp_hidden_dim)
        self.fc2 = nn.Linear(self.mlp_hidden_dim, self.mlp_input_dim)
        if regularization > 0.0:
            self.reg1 = nn.Dropout(regularization)
            self.reg2 = nn.Dropout(regularization)
        elif regularization == -1.0:
            self.reg1 = nn.BatchNorm1d(self.mlp_bn_dim)
            self.reg2 = nn.BatchNorm1d(self.mlp_bn_dim)
        else:
            self.reg1 = None
            self.reg2 = None

        if activation == 'gelu':
            self.act1 = nn.GELU()
        elif activation == 'mish':
            self.act1 = nn.Mish()
        else:
            raise ValueError('Unknown activation function type: %s'%activation)


    def forward(self, x):
        x = self.fc1(x)
        x = self.act1(x)
        if self.reg1 is not None:
            x = self.reg1(x)
        x = self.fc2(x)
        if self.reg2 is not None:
            x = self.reg2(x)
        return x


class MixerBlock(nn.Module):
    def __init__(self, tokens_mlp_dim, channels_mlp_dim, seq_len, hidden_dim, activation='gelu', regularization=0, r_se=4, 
                use_max_pooling=False, use_se=True):
        super().__init__()
        self.tokens_mlp_dim = tokens_mlp_dim
        self.channels_mlp_dim = channels_mlp_dim
        self.seq_len = seq_len
        self.hidden_dim = hidden_dim  # out channels of the conv
        self.mlp_block_token_mixing = MlpBlock(self.tokens_mlp_dim, self.seq_len, self.hidden_dim, activation=activation, 
                                                regularization=regularization)
        self.mlp_block_channel_mixing = MlpBlock(self.channels_mlp_dim, self.hidden_dim, self.seq_len, activation=activation, 
                                                regularization=regularization)
        self.use_se = use_se
        if self.use_se:
            self.se = SELayer(self.seq_len, r=r_se, use_max_pooling=use_max_pooling)

        self.LN1 = nn.LayerNorm(self.hidden_dim)
        self.LN2 = nn.LayerNorm(self.hidden_dim)

    def forward(self, x):
        # shape x [256, 8, 512] [bs, patches/time_steps, channels]
        y = self.LN1(x)
        y = y.transpose(1, 2)
        y = self.mlp_block_token_mixing(y)
        y = y.transpose(1, 2)
        if self.use_se:
            y = self.se(y)
        x = x + y
        y = self.LN2(x)
        y = self.mlp_block_channel_mixing(y)
        if self.use_se:
            y = self.se(y)
        return x + y


class MixerBlockTemporal(nn.Module):
    def __init__(self, tokens_mlp_dim, channels_mlp_dim, seq_len, hidden_dim, activation='gelu', regularization=0, r_se=4, 
                use_max_pooling=False, use_se=True):
        super().__init__()
        self.tokens_mlp_dim = tokens_mlp_dim
        self.channels_mlp_dim = channels_mlp_dim
        self.seq_len = seq_len
        self.hidden_dim = hidden_dim  # out channels of the conv
        self.mlp_block_channel_mixing = MlpBlock(self.channels_mlp_dim, self.hidden_dim, self.seq_len, activation=activation, 
                                                regularization=regularization)
        self.use_se = use_se
        if self.use_se:
            self.se = SELayer(self.seq_len, r=r_se, use_max_pooling=use_max_pooling)

        self.LN2 = nn.LayerNorm(self.hidden_dim)
        # self.LN22 = nn.LayerNorm(self.hidden_dim)

    def forward(self, x):
        # shape x [256, 8, 512] [bs, patches/time_steps, channels]
        y = self.LN2(x)
        y = self.mlp_block_channel_mixing(y)
        if self.use_se:
            y = self.se(y)

        return x + y


class MixerBlockSpatial(nn.Module):
    def __init__(self, tokens_mlp_dim, channels_mlp_dim, seq_len, hidden_dim, activation='gelu', regularization=0, r_se=4, 
                use_max_pooling=False, use_se=True):
        super().__init__()
        self.tokens_mlp_dim = tokens_mlp_dim
        self.channels_mlp_dim = channels_mlp_dim
        self.seq_len = seq_len
        self.hidden_dim = hidden_dim  # out channels of the conv
        self.mlp_block_token_mixing = MlpBlock(self.tokens_mlp_dim, self.seq_len, self.hidden_dim, activation=activation, 
                                                regularization=regularization)
        self.use_se = use_se
        if self.use_se:
            self.se = SELayer(self.seq_len, r=r_se, use_max_pooling=use_max_pooling)

        self.LN1 = nn.LayerNorm(self.hidden_dim)

    def forward(self, x):
        # shape x [256, 8, 512] [bs, patches/time_steps, channels]
        y = self.LN1(x)
        y = y.transpose(1, 2)
        y = self.mlp_block_token_mixing(y)
        y = y.transpose(1, 2)
        if self.use_se:
            y = self.se(y)
        return x + y


class MlpMixer(nn.Module):
    def __init__(self, num_classes, num_blocks, hidden_dim, tokens_mlp_dim, channels_mlp_dim, seq_len, activation='gelu', 
                mlp_block_type='normal', regularization=0, input_size=51, r_se=4, use_max_pooling=False, use_se=False):
        super().__init__()
        self.num_classes = num_classes
        self.num_blocks = num_blocks
        self.hidden_dim = hidden_dim
        self.seq_len = seq_len
        self.tokens_mlp_dim = tokens_mlp_dim
        self.channels_mlp_dim = channels_mlp_dim
        self.input_size = input_size #varyies with the number of joints
        # self.fc_in = nn.Linear(self.channels_mlp_dim, self.channels_mlp_dim)
        self.conv = nn.Conv1d(1, self.hidden_dim, (1, self.input_size), stride=1)
        self.activation = activation
        if mlp_block_type == 'normal':
            self.Mixer_Block = nn.ModuleList(MixerBlock(self.tokens_mlp_dim, self.channels_mlp_dim, self.seq_len, 
                                            self.hidden_dim, activation=self.activation, regularization=regularization, 
                                            r_se=r_se, use_max_pooling=use_max_pooling, use_se=use_se) for _ in range(num_blocks))
        elif mlp_block_type == 'temporal':
            self.Mixer_Block = nn.ModuleList(MixerBlockTemporal(self.tokens_mlp_dim, self.channels_mlp_dim, self.seq_len, 
                                            self.hidden_dim, activation=self.activation, regularization=regularization, 
                                            r_se=r_se, use_max_pooling=use_max_pooling, use_se=use_se) for _ in range(num_blocks))
        elif mlp_block_type == 'spatial':
            self.Mixer_Block = nn.ModuleList(MixerBlockSpatial(self.tokens_mlp_dim, self.channels_mlp_dim, self.seq_len, 
                                            self.hidden_dim, activation=self.activation, regularization=regularization, 
                                            r_se=r_se, use_max_pooling=use_max_pooling, use_se=use_se) for _ in range(num_blocks))
        else:
            raise ValueError('Unknown MLP-Block Type: %s'%mlp_block_type)
        self.LN = nn.LayerNorm(self.hidden_dim)
        self.fc_out = nn.Linear(self.hidden_dim, self.num_classes)


    def forward(self, x, padded):
        x = x.unsqueeze(1)
        y = self.conv(x)
        y = y.squeeze().transpose(1, 2)
        # [256, 8, 512] [bs, patches/time_steps, channels]
        for mb in self.Mixer_Block:
            y = mb(y)
        y = self.LN(y)
        # Exclude padded time steps from mean
        y = y * padded.view(padded.shape[0], padded.shape[1], 1)
        y = y.sum(dim=1)
        padded = padded.sum(dim=1)
        padded[padded == 0] = 1
        y = y / padded.view(-1, 1)

        out = self.fc_out(y)

        return out

