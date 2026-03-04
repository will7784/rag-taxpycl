var commonWRPath = '/common-1.0';

(function(){
	var propertiesCache = {};
	angular.module('cl.sii.settings',[
	])
	.constant('SiiDefault',{
		requestTimeout : 2000,
	})
	.service('SIISettings',function($http,$rootScope,$log){
		var token;
		//Se debe desacoplar de la carga del aa.js 
		if($rootScope.sdiSession && $rootScope.sdiSession.token){
			token=$rootScope.sdiSession.token;
		}else{
			if(typeof(getCookie) == "function"){
				token=getCookie('TOKEN');
			}
		}
		
		this.load = function(propertiesNameSpace, serviceNameSpace, dataServiceURL){
			var post = {
				method : 'POST'
				,url : dataServiceURL
				,headers : {
					'Content-Type' : 'application/json'
				}
				,data : {
					'metaData' : {
						'namespace': serviceNameSpace,
				        'conversationId': '1',
				        
				        'transactionId': token 
				      }
				}

			};
			$http(post).then(function(response){
				propertiesCache[propertiesNameSpace]=response.data.data;
				$rootScope.$broadcast(propertiesNameSpace+'.loaded', propertiesCache[propertiesNameSpace]);
			},function(response){
				$log.error("Error en consulta de properties para nameSpace:["+propertiesNameSpace+"] url:["+dataServiceURL+"]");
			});
		};
		this.get = function(propertiesNameSpace,propertyKey){
			//TODO agregar excepción en caso de no encontrarse properties o nameSpace
			return propertiesCache[propertiesNameSpace][propertyKey];
		}
	});


})();
