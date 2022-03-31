import pandapower as pp
import pandapower.plotting as plot
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
import base64
import io 

class MASHSG:
    """Distributed Intelligent System for SelfHealing in Smart Grids"""

    def __init__(
        self, 
        net: pp.pandapowerNet = None, 
        jsonNet: str = None, 
        debugView : str = None,
        ):
        '''
        Create a Intelligent Agent for Self Healing Grid

        Parameters:
        :net - pandapowerNet Grid
        :jsonNet - Json Grid File
        :debugView
            :None - disabled,
            :"Full" - Messages and Switchs
            :"Messages" - Only Messages
            :"Switchs" - Only Switchs
        '''

        # carrega em arquivo circuito dos ramais
        if (jsonNet is not None) and (net is None):
            net = pp.from_json(jsonNet)
        # circuito dos ramais
        self.net = net 
        # salva estado anterior das chaves para resetar simulação
        self.ini_closed = net.switch['closed'].values 
        # quadro negro de mensagens
        self.blackboard = []
        # instante da simulação
        self.t = 0 
        # tabelas de informações das chaves, montada no start_simu
        self.ssw = None 
        # DebugView
        self.debugView = debugView
        # barra com falta
        self.faultBus = -1
        # print columns
        self.__sswColumns = ['name', 'type', 'closed', 'vpu_from', 'vpu_to', 'ika']

    def begin(self) -> None:

        # reiniciando os estados das chaves
        self.net.switch['closed'] = self.ini_closed
        net = self.net
        # objeto de relatório para saída
        self.report=[]

        # iniciando a tabela de informações das chaves
        ssw = net.switch[['name','type','closed']]
        ssw['bus_from'] = net.line.loc[net.switch['element'],'from_bus'].values
        ssw['bus_to'] = net.line.loc[net.switch['element'],'to_bus'].values
        ssw['line'] = net.switch['element']

        # == Buscando as chaves vizinhas ==
        G = pp.topology.create_nxgraph(net,respect_switches=False)
        grupos_de=[]
        grupos_para=[]
        for sw_id, sw in net.switch.iterrows():
            #Busca em profundidade
            paths = nx.single_source_shortest_path(G,sw['bus'])
            # indices das chaves encontradas
            gd = []
            gp = []
            for k in paths:
                p = paths[k]
                # analisa caminho para encontra próxima chave
                dp = False
                for n in range(len(p)-1):
                    # informações da aresta (linha)
                    aresta = list(G[p[n]][p[n+1]].keys())
                    tipo = aresta[0][0]      
                    ln_id = aresta[0][1]
                    # não é chave, é linha
                    if (tipo != 'line'):
                        continue  
                    # não é chave, é o elemento PARA
                    if (ln_id == sw['element']):
                        dp=True
                        continue
                    # tem chave nesse elemento?
                    if (ln_id in net.switch['element'].values):
                        sw_v = net.switch.loc[net.switch['element'] == ln_id].index[0]
                        # qual é o grupo
                        if (dp):
                            if (sw_v not in gp):
                                gp.append(sw_v)
                        else:
                            if (sw_v not in gd):
                                gd.append(sw_v)
                        break
            # convertendo de lista para dicionário
            grupos_de.append(dict.fromkeys(gd,{}))
            grupos_para.append(dict.fromkeys(gp,{}))
            del gd
            del gp
        
        ssw['nb_from'] = grupos_para #time para
        ssw['nb_to'] = grupos_de #time de
        
        ssw['vpu_from'] = [0.0] * len(ssw) #tensão para
        ssw['vpu_to'] = [0.0] * len(ssw) #tensão de

        ssw['ika'] = [0.0] * len(ssw) #corrente na chave
        ssw['ika_max'] = [0.0] * len(ssw) #corrente máxima
        ssw['ika_pre'] = [0.0] * len(ssw) #corrente pré-falta
        ssw['ika_pos'] = [0.0] * len(ssw) #corrente pós-falta
        ssw['ika_rem'] = [0.0] * len(ssw) #corrente remanescente

        ssw['locked'] = [False] * len(ssw) #chave travada
        ssw['over_i'] = [False] * len(ssw) #sobrecorrente
        ssw['mode'] = [''] * len(ssw) #estado da chave

        self.ssw = ssw
        # iniciando quadronegro e instante
        self.blackboard = []
        self.t=0

    def draw(self, draw_bus_id : bool = False, destination = None) -> None:

        net = self.net

        cores = ['blue','orange','green','red','purple','cyan','pink','olive','cyan']

        collections = []

        collections.append(plot.create_bus_collection(net, net.ext_grid.bus.values, patch_type='rect', size=20, color='pink', zorder=1))
        collections.append(plot.create_line_collection(net, net.line.index, color='grey', zorder=2))
        
        mg = pp.topology.create_nxgraph(net, nogobuses=set(net.trafo.lv_bus.values) | set(net.trafo.hv_bus.values))
        for c, area in zip(cores, pp.topology.connected_components(mg)):
            collections.append(plot.create_bus_collection(net, area, size=5, color=c, zorder=3))

        collections.append(plot.create_line_switch_collection(net,size=30,distance_to_bus=40, color='black', zorder=4))

        t = self.t
        if t > 0:
            chaves = []

            for id, sw in self.ssw.iterrows():
                caption = sw['name']

                snd = [x for x in self.blackboard if x['sender'] == id and x['time'] == t]
                if len(snd) > 0:
                    caption += '➡'

                rec = [x for x in self.blackboard if x['recipient'] == id and x['time'] == t]
                if len(rec) > 0:
                    caption += '⬅'

                if len(sw['mode']) > 0:
                    caption += '[{0}]'.format(sw['mode'])
                chaves.append(caption)
        else:
            chaves = net.switch['name'].values

        if None not in chaves:
            bus_id = net.switch['bus']
            #bus_id = net.bus.iloc[bus_id].index.tolist()
            coords = zip(net.bus_geodata.x.loc[bus_id].values, net.bus_geodata.y.loc[bus_id].values)
            collections.append(plot.create_annotation_collection(texts=chaves, coords=coords, size=30, color='grey', zorder=5))

        if draw_bus_id:
            barras = [str(b) for b in net.bus.index]
            barCoor = zip(net.bus_geodata.x.values, net.bus_geodata.y.values)
            collections.append(plot.create_annotation_collection(texts=barras, coords=barCoor, size=20, color='navy', zorder=5))
        
        plot.draw_collections(collections)
        if destination == None:
            plt.show()
            return ''
        elif destination == 'HTML':
            s = io.BytesIO()
            plt.savefig(s,  format='png')
            plt.close()
            img = base64.b64encode(s.getvalue()).decode("utf-8").replace("\n", "")
            return '<img src="data:image/png;base64, %s">' % img
        else:
            plt.savefig(destination)
            plt.close()
            return ''
    
    def __str__(self) -> str:
        return f'SMA=[switchs({self.net.switch.shape[0]}),grids({self.net.ext_grid.shape[0]}),buses({self.net.bus.shape[0]})]'
 
    def __pflow(self) -> None:
        '''PowerFlow'''

        pp.runpp(self.net, neglect_open_switch_branches=True)

    def __level2(self) -> None:
        '''
            Level 2 - mensuraments current and tension
        '''
        
        self.ssw['vpu_from'] = self.net.res_bus.loc[self.ssw['bus_from'],'vm_pu'].fillna(0).values
        self.ssw['vpu_to'] = self.net.res_bus.loc[self.ssw['bus_to'],'vm_pu'].fillna(0).values
        self.ssw['ika'] = self.net.res_line.loc[self.ssw['line'],'i_ka'].fillna(0).values
        self.ssw['over_i'] = [ (x[0] < x[1]) for x in self.ssw[ ['ika_max','ika_pos'] ].values ]

    def setFaultBus(
        self, 
        faultBus : int, 
        max_pw : float = 0.08, 
        pre_pw : float = 0.04,
        ) -> None:
        '''
        Set Fault Bus
        '''

        # Calculando corrente máxima
        self.net.load.loc[:,'p_mw'] = max_pw
        self.net.load.loc[:,'q_mvar'] = max_pw/10
        self.__pflow()
        max_ka = self.net.res_line.loc[self.net.switch['element'],'i_ka'].values
        max_ka = [round(x,2)+0.01 for x in max_ka]
        self.ssw['ika_max'] = max_ka

        #potência nominal
        self.net.load.loc[:,'p_mw'] = pre_pw
        self.net.load.loc[:,'q_mvar'] = pre_pw/10
        self.__pflow()
        self.ssw['ika_pre'] = self.net.res_line.loc[self.net.switch['element'],'i_ka'].values

        self.ssw.loc[ (self.ssw['vpu_from'] > 0) & (self.ssw['vpu_to'] > 0) & (self.ssw['closed'] == False), 'locked'] = True

        #remanescente
        self.ssw['ika_rem'] = max_ka - self.ssw['ika_pre']

        self.__level2()
        self.report.append('<hr>\r\n')
        self.report.append('<h1>Start Grid</h1>\r\n')

        if self.debugView == 'Full' or self.debugView == 'Switchs':
            self.report.append('<h2>Smart Switchs</h2>\r\n')
            self.report.append(self.ssw[self.__sswColumns].to_html())

        self.report.append(self.draw(draw_bus_id=True, destination='HTML'))

        # injetando CC
        self.faultBus = faultBus
        self.net.load.loc[self.net.load['bus'] == faultBus,'p_mw'] = 1.0
        self.__pflow()
        self.ssw['ika_pos'] = self.net.res_line.loc[self.net.switch['element'],'i_ka'].values

        self.__level2()
        self.report.append('<hr>\r\n')
        self.report.append('<h1>Fault Create</h1>\r\n')
        self.report.append('<h2>Fault create in bus %s.</h2>\r\n' % faultBus)

        if self.debugView == 'Full' or self.debugView == 'Switchs':
            self.report.append('<h2>Smart Switchs</h2>\r\n')
            self.report.append(self.ssw[self.__sswColumns].to_html())

    def __haveMsg(self, id, cmd):
        ssw = self.ssw
        ii_to = [p for p in ssw.at[id,'nb_to'] if 'cmd' in ssw.at[id,'nb_to'][p].keys() and ssw.at[id,'nb_to'][p]['cmd'] == cmd]
        ii_from = [p for p in ssw.at[id,'nb_from'] if 'cmd' in ssw.at[id,'nb_from'][p].keys() and ssw.at[id,'nb_from'][p]['cmd'] == cmd]
        return (len(ii_to) > 0 or len(ii_from) > 0)

    def step(self) -> bool:
        ssw = self.ssw
        net = self.net
        t = self.t
        blackboard = self.blackboard

        # listando as chaves
        for id in ssw.index:

            vizinhos = list(ssw.at[id,'nb_from'].keys()) + list(ssw.at[id,'nb_to'].keys())

            #Nivel 1
            if ssw.at[id,'over_i'] and ssw.at[id,'mode'] == '':

                if ssw.at[id,'type'] == 'CB' and ssw.at[id,'closed']: #SUBESTACAO
                    ssw.at[id,'closed'] = False
                    ssw.at[id,'mode'] = 'SelfHealing'

                    for key in vizinhos:
                        blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'SearchFault', 'value':''})

            #mensagens recebidas para a chave(id) naquele instante(t)
            filterMsgs = [m for m in blackboard if m['recipient'] == id and m['time'] == t]

            for msg in filterMsgs:

                # pergunta se tem sobre corrente (pag 70)
                if msg['cmd'] == 'SearchFault':

                    if ssw.at[id,'mode'] != 'SelfHealing':

                        value = bool(ssw.at[id,'over_i'])
                        blackboard.append({'time':(t+1), 'sender':id, 'recipient':msg['sender'], 'cmd':'IsFault', 'value':value})

                        if value:
                            for key in vizinhos:
                                if msg['sender'] != key:
                                    blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'SearchFault', 'value':''})
                
                if msg['cmd'] == 'AreaIsolate':
                    
                    if ssw.at[id,'closed']:
                        ssw.at[id,'closed'] = False
                        ssw.at[id,'mode'] = 'IsolateSwitch'
                    
                    for key in vizinhos:
                        blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'IsolateInfo', 'value':''})

                if msg['cmd'] == 'AreaHelp':
                    
                    bv_from = bool(ssw.at[id,'vpu_from'] < 0.001)
                    bv_to = bool(ssw.at[id,'vpu_to'] < 0.001)

                    xorVpu = bv_from ^ bv_to
                    if ssw.at[id,'mode'] not in ['IsolateSwitch','FaultIsolate']:

                        if xorVpu and not ssw.at[id,'closed']:

                            ssw.at[id,'closed'] = True
                            ssw.at[id,'mode'] = 'HelpSwitch'
                        else:
                            # busca o vizinho que entregou a maior corrente remanescente
                            for nb in ['nb_to','nb_from']:
                                if msg['sender'] in ssw.at[id,nb]:
                                    continue # não reenviar para origem
                                
                                gr = ssw.at[id,nb]
                                if len(gr) == 0:
                                    continue
                                
                                # lista id_chave e corrente dos vizinhos posteiores
                                ika_rem = { p:gr[p]['value'] for p in gr if gr[p]['cmd'] == 'IkARemai' }
                                if len(ika_rem) == 0:
                                    continue

                                key_max = max(ika_rem, key=ika_rem.get) # id da máxima corrente
                                blackboard.append({'time':(t+1), 'sender':id, 'recipient':key_max, 'cmd':msg['cmd'], 'value':''})

                if msg['cmd'] == 'IsolateInfo':

                    bv_from = bool(ssw.at[id,'vpu_from'] < 0.001)
                    bv_to = bool(ssw.at[id,'vpu_to'] < 0.001)

                    xorVpu = bv_from ^ bv_to

                    if xorVpu and ssw.at[id,'mode'] == 'SelfHealing':
                        ssw.at[id,'closed'] = True

                    elif xorVpu and ssw.at[id,'mode'] not in ['IsolateSwitch','FaultIsolate']:
                        for key in vizinhos:
                            if msg['sender'] != key:
                                blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'SearchRemai', 'value':''})

                    else:

                        if not self.__haveMsg(id,'IsolateInfo'):
                            for key in vizinhos:
                                if msg['sender'] != key:
                                    blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'IsolateInfo', 'value':''})

                if msg['cmd'] == 'SearchRemai':
                    
                    if ssw.at[id,'mode'] not in ['IsolateSwitch','FaultIsolate']:
                        if ssw.at[id,'type'] == 'CB' and ssw.at[id,'closed']: #SUBESTACAO
                            ssw.at[id,'mode'] = 'CheckRemai'
                            value = ssw.at[id,'ika_rem']
                            # reenvia ao anteiror a corrente remanescente
                            blackboard.append({'time':(t+1), 'sender':id, 'recipient':msg['sender'], 'cmd':'IkARemai', 'value':value})

                        else:

                            if not self.__haveMsg(id,'SearchRemai'):
                                for key in vizinhos:
                                    if msg['sender'] != key:
                                        blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'SearchRemai', 'value':''})

                if msg['cmd'] == 'IkARemai':
                    
                    if ssw.at[id,'mode'] != 'FaultIsolate':

                        if not self.__haveMsg(id,'IkARemai'):
                            value = min(ssw.at[id,'ika_rem'], msg['value'])

                            for key in vizinhos:
                                if msg['sender'] != key:
                                    blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'IkARemai', 'value':value})

                # salva comando no vizinho que enviou
                if msg['sender'] in ssw.at[id,'nb_to'].keys():
                    ssw.at[id,'nb_to'][ msg['sender']] = {'cmd':msg['cmd'], 'value':msg['value']}
                else:
                    ssw.at[id,'nb_from'][ msg['sender']] = {'cmd':msg['cmd'], 'value':msg['value']}

                # analisa respostas dos vizinhos
                for nb in ['nb_to','nb_from']:
                    gr = ssw.at[id,nb]
                    num_nb = len(gr.keys())
                    
                    if num_nb == 0:
                        continue

                    # chaves com respostas
                    resps = [ p for p in gr if len(gr[p].keys()) > 0]

                    if len(resps) < num_nb:
                        # aguardando respostas
                        continue

                    # busca da regiao sem falta
                    num_NoFault = len([ p for p in gr if gr[p]['cmd'] == 'IsFault' and not gr[p]['value'] ])
                    if num_nb == num_NoFault:
                        if not ssw.at[id,'locked'] :
                            # se abre
                            ssw.at[id,'closed'] = False
                            ssw.at[id,'mode'] = 'FaultIsolate'

                            for key in gr.keys():
                                # manda abrir as chaves vizinhas
                                blackboard.append({'time':(t+1), 'sender':id, 'recipient':key, 'cmd':'AreaIsolate', 'value':''})

                    # religamento da chave de socorro
                    if ssw.at[id,'mode'] == 'IsolateSwitch':
                        # qual a maior corrente remanescente
                        ika_rem = { p:gr[p]['value'] for p in gr if gr[p]['cmd'] == 'IkARemai' }

                        if num_nb == len(ika_rem):
                            key_maxrem = max(ika_rem, key=ika_rem.get)
                            blackboard.append({'time':(t+1), 'sender':id, 'recipient':key_maxrem, 'cmd':'AreaHelp', 'value':''})


            # repassa comando de fechar ao circuito se não travado
            if not ssw.at[id,'locked'] and (net.switch.at[id,'closed'] != ssw.at[id,'closed']):
                net.switch.at[id,'closed'] = ssw.at[id,'closed']

        self.t += 1

        self.__pflow()

        self.__level2()

        swid = {id:sw['name'] for id,sw in ssw.iterrows()}
        bbt = [{'sender':swid[m['sender']], 'recipient':swid[m['recipient']], 'cmd':m['cmd'], 'value':m['value']}  for m in blackboard if m['time'] == t]
        bbdf = pd.DataFrame(bbt)

        self.report.append('<p style=\"page-break-before: always\">\r\n')
        self.report.append('<hr>\r\n')
        self.report.append(f'<h1>Step {self.t}</h1>\r\n')
        
        # exibe tabelas se definido
        if self.debugView == 'Full' or self.debugView == 'Messages' :

            if len(bbdf) > 0:
                self.report.append('<h2>Blackboard</h2>\r\n')
                self.report.append(bbdf.to_html())
            else:
                self.report.append('<p>No new messages.</p>\r\n')

        if self.debugView == 'Full' or self.debugView == 'Switchs':
            self.report.append('<h2>Smart Switchs</h2>\r\n')
            self.report.append(self.ssw[self.__sswColumns].to_html())
            
        self.report.append(self.draw(destination='HTML'))

        return not (self.t > 1 and len(bbdf)==0)

    def to_html(self) -> str:
        html = ''
        for line in self.report:
            html += line + '\r\n'
        return html
    def _repr_html_(self):
        return to_html(self)